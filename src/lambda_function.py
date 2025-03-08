import json
import os
from datetime import datetime
from typing import Dict, Any, List, Tuple, TypedDict, Optional
import requests
import boto3
from dataclasses import dataclass
from functools import lru_cache
from enum import Enum

class UpdateMode(Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"

class IpcData(TypedDict):
    rate: float
    date: str
    mode: str  # "monthly" or "yearly"

class CategoryData(TypedDict):
    goal_target: float
    name: str
    note: str

@dataclass
class CategoryUpdate:
    category_id: str
    category_name: str
    status: str
    old_target: Optional[int] = None
    new_target: Optional[int] = None
    note: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    ynab_response: Optional[Dict[str, Any]] = None

@lru_cache(maxsize=1)
def get_ssm_parameter(param_name: str) -> str:
    """Get parameter from AWS Systems Manager Parameter Store with caching."""
    ssm = boto3.client('ssm')
    response = ssm.get_parameter(
        Name=param_name,
        WithDecryption=True
    )
    return response['Parameter']['Value']

def get_category_ids() -> List[str]:
    """Get list of category IDs from SSM parameter."""
    category_ids_str = get_ssm_parameter('/ynab/category_ids')
    return [id.strip() for id in category_ids_str.split(',')]

@lru_cache(maxsize=1)
def get_update_mode() -> UpdateMode:
    """Get update mode from SSM parameter, defaults to monthly if not set."""
    try:
        mode = get_ssm_parameter('/ynab/update_mode')
        mode = mode.lower()
        if mode == UpdateMode.YEARLY.value:
            return UpdateMode.YEARLY
        return UpdateMode.MONTHLY
    except:
        return UpdateMode.MONTHLY

def get_ipc_rate() -> IpcData:
    """Get the IPC rate based on configured update mode."""
    # For monthly updates: Uses month-over-month variation rate (IPC251855)
    # For yearly updates: Uses year-over-year variation rate from December (IPC251858)
    update_mode = get_update_mode()
    
    if update_mode == UpdateMode.MONTHLY:
        return get_monthly_ipc_rate()
    else:
        return get_yearly_ipc_rate()

def get_monthly_ipc_rate() -> IpcData:
    """Get the latest monthly IPC rate from INE (month-over-month variation)."""
    # Get the last 3 months to ensure we have at least one with "Definitivo" status
    url = "https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE/IPC251855?nult=3&tip=A"
    response = requests.get(url, verify=True)
    response.raise_for_status()
    data = response.json()
    
    print(f"Monthly IPC data received: {json.dumps(data['Data'])}")
    
    # Find the most recent data point with "Definitivo" status that is not in the future
    current_date = datetime.now()
    print(f"Current date for comparison: {current_date.strftime('%Y-%m-%d %H:%M:%S')}")
    definitivo_data = None
    
    # Sort data points by date in descending order (most recent first)
    sorted_data = sorted(
        data["Data"],
        key=lambda x: datetime.strptime(x["Fecha"].split("T")[0], "%Y-%m-%d"),
        reverse=True
    )
    
    print(f"Sorted data points by date (descending):")
    for i, point in enumerate(sorted_data):
        point_date = datetime.strptime(point["Fecha"].split("T")[0], "%Y-%m-%d")
        print(f"  {i+1}. {point_date.strftime('%Y-%m-%d')} - {point['T3_TipoDato']} - {point['Valor']}")
    
    for point in sorted_data:
        point_date = datetime.strptime(point["Fecha"].split("T")[0], "%Y-%m-%d")
        # Skip future dates
        if point_date > current_date:
            print(f"Skipping future date: {point_date.strftime('%Y-%m-%d')} (current date: {current_date.strftime('%Y-%m-%d')})")
            continue
            
        if point["T3_TipoDato"] == "Definitivo":
            definitivo_data = point
            print(f"Selected monthly data point: {point_date.strftime('%Y-%m-%d')} with value {point['Valor']}")
            break
    
    if not definitivo_data:
        raise ValueError("Could not find any IPC data with 'Definitivo' status for a non-future date")
    
    result = {
        "rate": float(definitivo_data["Valor"]),
        "date": datetime.strptime(definitivo_data["Fecha"].split("T")[0], "%Y-%m-%d").strftime("%Y-%m"),
        "mode": UpdateMode.MONTHLY.value
    }
    
    print(f"Returning IPC data: {json.dumps(result)}")
    return result

def get_yearly_ipc_rate() -> IpcData:
    """Get the year-over-year IPC rate from INE using December's value."""
    # Get last 3 months to ensure we have December's data
    url = "https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE/IPC251858?nult=3&tip=A"
    response = requests.get(url, verify=True)
    response.raise_for_status()
    data = response.json()
    
    print(f"Yearly IPC data received: {json.dumps(data['Data'])}")
    
    if len(data["Data"]) < 3:  # We request 3 months, so we should get 3 months
        raise ValueError("Could not get enough data points for yearly calculation")
    
    # Find the most recent December data point that is not in the future
    current_date = datetime.now()
    print(f"Current date for comparison: {current_date.strftime('%Y-%m-%d %H:%M:%S')}")
    december_point = None
    
    # Sort data points by date in descending order (most recent first)
    sorted_data = sorted(
        data["Data"],
        key=lambda x: datetime.strptime(x["Fecha"].split("T")[0], "%Y-%m-%d"),
        reverse=True
    )
    
    print(f"Sorted data points by date (descending):")
    for i, point in enumerate(sorted_data):
        point_date = datetime.strptime(point["Fecha"].split("T")[0], "%Y-%m-%d")
        print(f"  {i+1}. {point_date.strftime('%Y-%m-%d')} - {point.get('T3_TipoDato', 'Unknown')} - {point['Valor']}")
    
    for point in sorted_data:
        point_date = datetime.strptime(point["Fecha"].split("T")[0], "%Y-%m-%d")
        # Skip future dates
        if point_date > current_date:
            print(f"Skipping future date: {point_date.strftime('%Y-%m-%d')} (current date: {current_date.strftime('%Y-%m-%d')})")
            continue
            
        if point_date.month == 12 and point.get("T3_TipoDato", "") == "Definitivo":
            december_point = (point_date, float(point["Valor"]))
            print(f"Selected December data point: {point_date.strftime('%Y-%m-%d')} with value {point['Valor']}")
            break
    
    if not december_point:
        raise ValueError("Could not find December's IPC value with 'Definitivo' status for a non-future date")
    
    # Use December's value which is already year-over-year rate
    december_date, rate = december_point
    
    result = {
        "rate": rate,
        "date": december_date.strftime("%Y"),  # Use December's year
        "mode": UpdateMode.YEARLY.value
    }
    
    print(f"Returning IPC data: {json.dumps(result)}")
    return result

def get_category_data(budget_id: str, category_id: str, ynab_token: str) -> CategoryData:
    """Get category data from YNAB in a single API call."""
    url = f"https://api.ynab.com/v1/budgets/{budget_id}/categories/{category_id}"
    headers = {"Authorization": f"Bearer {ynab_token}"}
    response = requests.get(url, headers=headers)
    category = response.json()['data']['category']
    return {
        "goal_target": float(category['goal_target']),
        "name": category.get('name', 'Unknown Category'),
        "note": category.get('note', '')
    }

def format_ipc_message(current_target: int, new_target: int, ipc_rate: float, period: str, mode: str) -> str:
    """Format IPC message for YNAB notes."""
    # Convert from millicents to euros and display with two decimal places
    current_euros = current_target / 1000
    new_euros = new_target / 1000
    mode_prefix = "Annual" if mode == UpdateMode.YEARLY.value else "Monthly"
    rate_type = "year-over-year" if mode == UpdateMode.YEARLY.value else "month-over-month"
    return f"{period} {mode_prefix} IPC update: {current_euros:.2f}€ → {new_euros:.2f}€ ({ipc_rate:.1f}% {rate_type})"

def is_update_needed(current_notes: str, period: str) -> bool:
    """Check if we need to update for this period."""
    if not current_notes:
        return True
    
    # Check if the note contains the period
    return period not in current_notes.split('\n')[0]

def send_notification(subject: str, message: str) -> None:
    """Send SNS notification."""
    sns = boto3.client('sns')
    topic_arn = os.environ['NOTIFICATION_TOPIC_ARN']
    sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)

def update_category(budget_id: str, category_id: str, ynab_token: str, ipc_data: IpcData) -> CategoryUpdate:
    """Update a single category target with IPC rate adjustment."""
    try:
        # Get category data in a single API call
        category_data = get_category_data(budget_id, category_id, ynab_token)
        
        # Check if update is needed
        if not is_update_needed(category_data["note"], ipc_data["date"]):
            return CategoryUpdate(
                category_id=category_id,
                category_name=category_data["name"],
                status="skipped",
                message=f"Already updated for period {ipc_data['date']}",
                note=category_data["note"].split('\n')[0] if category_data["note"] else 'No previous updates'
            )
        
        # Calculate new target (all in millicents)
        current_millicents = int(category_data["goal_target"])
        current_euros = current_millicents / 1000
        new_euros = current_euros * (1 + ipc_data["rate"] / 100)
        new_millicents = int(round(new_euros * 1000 / 1000) * 1000)  # Round to nearest euro
        
        # Format message and update category
        ipc_message = format_ipc_message(
            current_millicents,
            new_millicents,
            ipc_data["rate"],
            ipc_data["date"],
            ipc_data["mode"]
        )
        new_notes = f"{ipc_message}\n{category_data['note']}"
        
        url = f"https://api.ynab.com/v1/budgets/{budget_id}/categories/{category_id}"
        headers = {
            "Authorization": f"Bearer {ynab_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "category": {
                "goal_target": new_millicents,
                "note": new_notes
            }
        }
        
        response = requests.patch(url, headers=headers, json=payload)
        return CategoryUpdate(
            category_id=category_id,
            category_name=category_data["name"],
            status="updated",
            old_target=current_millicents,  # Keep in millicents for consistency
            new_target=new_millicents,      # Keep in millicents for consistency
            note=ipc_message,
            ynab_response=response.json()
        )
        
    except Exception as e:
        return CategoryUpdate(
            category_id=category_id,
            category_name="Unknown",
            status="error",
            error=str(e)
        )

def update_ynab_targets(ipc_data: IpcData) -> Dict[str, Any]:
    """Update multiple YNAB category targets with IPC rate adjustment."""
    # Get parameters from SSM
    ynab_token = get_ssm_parameter('/ynab/token')
    budget_id = get_ssm_parameter('/ynab/budget_id')
    category_ids = get_category_ids()
    
    # Process all categories
    results = [update_category(budget_id, category_id, ynab_token, ipc_data)
              for category_id in category_ids]
    
    # Prepare notification message
    notification_lines = [f"IPC Update Results for {ipc_data['date']} (Rate: {ipc_data['rate']}%)\n"]
    
    for result in results:
        if result.status == "updated":
            # Convert from millicents to euros for display
            old_euros = result.old_target / 1000
            new_euros = result.new_target / 1000
            notification_lines.append(
                f"✅ {result.category_name}: {old_euros:.2f}€ -> {new_euros:.2f}€"
            )
        elif result.status == "skipped":
            notification_lines.append(
                f"⏭️ {result.category_name}: {result.message}"
            )
        else:
            notification_lines.append(
                f"❌ {result.category_name}: Error - {result.error}"
            )
    
    send_notification(
        subject=f"IPC Update Results - {ipc_data['date']}",
        message="\n".join(notification_lines)
    )
    
    return {"results": [vars(r) for r in results]}

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler function."""
    try:
        # Check if we should run based on update mode
        update_mode = get_update_mode()
        current_date = datetime.now()
        
        print(f"Current date: {current_date.strftime('%Y-%m-%d')}, Update mode: {update_mode.value}")
        
        if update_mode == UpdateMode.YEARLY and current_date.month != 1:
            print(f"Skipping yearly update in month {current_date.month}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Skipped: Yearly updates only run in January',
                    'update_mode': update_mode.value,
                    'current_month': current_date.month
                })
            }
        
        # Get IPC rate
        try:
            ipc_data = get_ipc_rate()
            print(f"Using IPC data: {json.dumps(ipc_data)}")
        except Exception as e:
            print(f"Error getting IPC rate: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Failed to get IPC rate: {str(e)}'
                })
            }
        
        # Update YNAB targets
        try:
            ynab_response = update_ynab_targets(ipc_data)
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'monthly_rate': ipc_data["rate"],
                    'period': ipc_data["date"],
                    'timestamp': datetime.now().isoformat(),
                    'results': ynab_response["results"],
                    'update_mode': update_mode.value,
                    'current_month': current_date.month
                })
            }
        except Exception as e:
            print(f"Error updating YNAB: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Failed to update YNAB: {str(e)}'
                })
            }
        
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Unexpected error: {str(e)}'
            })
        }