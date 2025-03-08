import json
import os
from datetime import datetime
from typing import Dict, Any, List, Tuple, TypedDict, Optional
import requests
import boto3
from dataclasses import dataclass
from functools import lru_cache

class IpcData(TypedDict):
    rate: float
    date: str

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

def get_ipc_rate() -> IpcData:
    """Get the latest IPC monthly rate from INE."""
    url = "https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE/IPC251858?nult=1&tip=A"
    response = requests.get(url, verify=True)
    response.raise_for_status()
    data = response.json()
    latest_data = data["Data"][0]
    return {
        "rate": float(latest_data["Valor"]),
        "date": datetime.strptime(latest_data["Fecha"].split("T")[0], "%Y-%m-%d").strftime("%Y-%m")
    }

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

def format_ipc_message(current_target: int, new_target: int, ipc_rate: float, period: str) -> str:
    """Format IPC message for YNAB notes."""
    # Convert from millicents to euros and display with two decimal places
    current_euros = current_target / 1000
    new_euros = new_target / 1000
    return f"{period} IPC: {ipc_rate}%: {current_euros:.2f}€ -> {new_euros:.2f}€"

def is_update_needed(current_notes: str, period: str) -> bool:
    """Check if we need to update for this period."""
    if not current_notes:
        return True
    latest_period = current_notes.split('\n')[0].split()[0] if current_notes.split('\n') else ''
    return latest_period != period

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
        # First convert to euros, apply rate, then round to nearest euro (1000 millicents)
        current_euros = current_millicents / 1000
        new_euros = current_euros * (1 + ipc_data["rate"] / 100)
        new_millicents = int(round(new_euros * 1000 / 1000) * 1000)  # Round to nearest euro
        
        # Format message and update category
        ipc_message = format_ipc_message(current_millicents, new_millicents, ipc_data["rate"], ipc_data["date"])
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
        # Get IPC rate
        try:
            ipc_data = get_ipc_rate()
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
                    'results': ynab_response["results"]
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