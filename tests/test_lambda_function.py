import json
import pytest
from unittest.mock import patch, MagicMock
from src.lambda_function import (
    get_ipc_rate,
    get_monthly_ipc_rate,
    get_yearly_ipc_rate,
    get_update_mode,
    UpdateMode,
    get_category_data,
    format_ipc_message,
    update_category,
    update_ynab_targets,
    lambda_handler,
    get_category_ids,
    send_notification
)
from datetime import datetime, UTC
import requests
import os

@pytest.fixture
def mock_ssm():
    with patch('boto3.client') as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        mock_client.get_parameter.return_value = {'Parameter': {'Value': 'test_value'}}
        yield mock_client

@pytest.fixture
def mock_requests():
    with patch('requests.get') as mock_get, \
         patch('requests.patch') as mock_patch:
        yield mock_get, mock_patch

@pytest.fixture
def mock_sns():
    with patch('boto3.client') as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        yield mock_client

def test_get_ipc_rate():
    """Test getting IPC rate from INE."""
    with patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {
            "Data": [{"Valor": "0.2", "Fecha": "2025-01-01T00:00:00"}]
        }
        result = get_ipc_rate()
        assert result["rate"] == 0.2
        assert result["date"] == "2025-01"

def test_get_category_ids():
    """Test getting category IDs from SSM."""
    with patch('boto3.client') as mock_boto3:
        mock_ssm = MagicMock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {
            'Parameter': {'Value': 'category1,category2, category3 '}
        }
        
        result = get_category_ids()
        assert result == ['category1', 'category2', 'category3']
        mock_ssm.get_parameter.assert_called_once_with(
            Name='/ynab/category_ids',
            WithDecryption=True
        )

def test_get_category_data():
    """Test getting category data from YNAB."""
    with patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {
            'data': {
                'category': {
                    'goal_target': 100400,
                    'name': 'Test Category',
                    'note': 'Test note'
                }
            }
        }
        result = get_category_data('budget_id', 'category_id', 'token')
        assert result['goal_target'] == 100400
        assert result['name'] == 'Test Category'
        assert result['note'] == 'Test note'

def test_format_ipc_message():
    """Test formatting IPC message."""
    result = format_ipc_message(1004000, 1006000, 0.2, '2025-01', 'monthly')
    assert result == '2025-01 Monthly IPC: 0.20%: 1004.00€ -> 1006.00€'

def test_update_category():
    """Test updating a single category."""
    with patch('requests.get') as mock_get, \
         patch('requests.patch') as mock_patch:
        
        # Mock get_category_data response
        mock_get.return_value.json.return_value = {
            'data': {
                'category': {
                    'goal_target': 1004000,  # 1004.00€ in millicents
                    'name': 'Test Category',
                    'note': ''
                }
            }
        }
        
        # Mock update response
        mock_patch.return_value.json.return_value = {'data': {'category': {'id': 'category_id'}}}
        
        result = update_category(
            'budget_id',
            'category_id',
            'token',
            {'date': '2025-01', 'rate': 0.2, 'mode': 'monthly'}
        )
        
        assert result.status == 'updated'
        assert result.category_name == 'Test Category'
        assert result.old_target == 1004000  # Keep in millicents
        assert result.new_target == 1006000  # 1004.00 * 1.002 = 1006.008, rounded to 1006.00

def test_update_category_rounding():
    """Test that amounts are rounded to the nearest euro."""
    with patch('requests.get') as mock_get, \
         patch('requests.patch') as mock_patch:
        
        # Mock get_category_data response with an amount that will result in a decimal
        mock_get.return_value.json.return_value = {
            'data': {
                'category': {
                    'goal_target': 1000000,  # 1000.00€ in millicents
                    'name': 'Test Category',
                    'note': ''
                }
            }
        }
        
        # Mock update response
        mock_patch.return_value.json.return_value = {'data': {'category': {'id': 'category_id'}}}
        
        result = update_category(
            'budget_id',
            'category_id',
            'token',
            {'date': '2025-01', 'rate': 0.616, 'mode': 'monthly'}  # This will result in 1006.16€, rounded to 1006.00€
        )
        
        assert result.status == 'updated'
        assert result.category_name == 'Test Category'
        assert result.old_target == 1000000  # Keep in millicents
        assert result.new_target == 1006000  # 1000.00 * 1.00616 = 1006.16, rounded to 1006.00

def test_update_category_skip():
    """Test skipping category update when already updated."""
    with patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {
            'data': {
                'category': {
                    'goal_target': 100400,
                    'name': 'Test Category',
                    'note': '2025-01 IPC: 0.2%: Previous update'
                }
            }
        }
        
        result = update_category(
            'budget_id',
            'category_id',
            'token',
            {'date': '2025-01', 'rate': 0.2}
        )
        
        assert result.status == 'skipped'
        assert result.category_name == 'Test Category'

def test_update_ynab_targets():
    """Test updating multiple categories."""
    with patch('src.lambda_function.get_ssm_parameter') as mock_ssm, \
         patch('src.lambda_function.get_category_ids') as mock_get_ids, \
         patch('requests.get') as mock_get, \
         patch('requests.patch') as mock_patch, \
         patch('src.lambda_function.send_notification') as mock_notify:
        
        # Mock SSM parameters
        mock_ssm.side_effect = ['token', 'budget_id']
        mock_get_ids.return_value = ['category1', 'category2']
        
        # Mock YNAB API responses
        mock_get.return_value.json.return_value = {
            'data': {'category': {'goal_target': 1004000, 'name': 'Category 1', 'note': ''}}
        }
        
        # Mock update responses
        mock_patch.return_value.json.return_value = {'data': {'category': {'id': 'category_id'}}}
        
        result = update_ynab_targets({'date': '2025-01', 'rate': 0.2, 'mode': 'monthly'})
        
        assert len(result['results']) == 2
        assert result['results'][0]['category_name'] == 'Category 1'
        assert result['results'][0]['old_target'] == 1004000  # Keep in millicents
        assert result['results'][0]['new_target'] == 1006000  # 1004.00 * 1.002 = 1006.008, rounded to 1006.00
        assert result['results'][1]['category_name'] == 'Category 1'
        assert result['results'][1]['old_target'] == 1004000  # Keep in millicents
        assert result['results'][1]['new_target'] == 1006000  # 1004.00 * 1.002 = 1006.00
        
        # Verify notification was sent with correct euro amounts
        mock_notify.assert_called_once()
        notification_message = mock_notify.call_args[1]['message']
        assert 'Category 1: 1004.00€ -> 1006.00€' in notification_message

def test_update_ynab_targets_all_skipped():
    """Test updating multiple categories when all are skipped."""
    with patch('src.lambda_function.get_ssm_parameter') as mock_ssm, \
         patch('src.lambda_function.get_category_ids') as mock_get_ids, \
         patch('requests.get') as mock_get, \
         patch('src.lambda_function.send_notification') as mock_notify:
        
        # Mock SSM parameters
        mock_ssm.side_effect = ['token', 'budget_id']
        mock_get_ids.return_value = ['category1', 'category2']
        
        # Mock YNAB API responses
        mock_get.return_value.json.side_effect = [
            {'data': {'category': {'goal_target': 100400, 'name': 'Category 1', 'note': '2025-01 IPC: 0.2%: Previous update'}}},
            {'data': {'category': {'goal_target': 200000, 'name': 'Category 2', 'note': '2025-01 IPC: 0.2%: Previous update'}}}
        ]
        
        result = update_ynab_targets({'date': '2025-01', 'rate': 0.2})
        
        assert len(result['results']) == 2
        assert all(r['status'] == 'skipped' for r in result['results'])
        
        # Verify notification was sent
        mock_notify.assert_called_once()
        notification_message = mock_notify.call_args[1]['message']
        assert 'Category 1' in notification_message
        assert 'Category 2' in notification_message
        assert 'Already updated for period' in notification_message

def test_lambda_handler_success():
    """Test successful lambda handler execution."""
    with patch('src.lambda_function.get_ipc_rate') as mock_ipc, \
         patch('src.lambda_function.update_ynab_targets') as mock_update:
        
        mock_ipc.return_value = {'rate': 0.2, 'date': '2025-01'}
        mock_update.return_value = {
            'results': [
                {
                    'category_name': 'Test Category',
                    'status': 'updated',
                    'old_target': 1004,
                    'new_target': 1006
                }
            ]
        }
        
        result = lambda_handler({}, None)
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'results' in body
        assert len(body['results']) == 1
        assert body['results'][0]['status'] == 'updated'

def test_lambda_handler_ipc_error():
    """Test lambda handler with IPC rate error."""
    with patch('src.lambda_function.get_ipc_rate') as mock_ipc:
        mock_ipc.side_effect = Exception('IPC API error')
        
        result = lambda_handler({}, None)
        
        assert result['statusCode'] == 500
        assert 'Failed to get IPC rate' in json.loads(result['body'])['error']

def test_lambda_handler_update_error():
    """Test lambda handler with YNAB update error."""
    with patch('src.lambda_function.get_ipc_rate') as mock_ipc, \
         patch('src.lambda_function.update_ynab_targets') as mock_update:
        
        mock_ipc.return_value = {'rate': 0.2, 'date': '2025-01'}
        mock_update.side_effect = Exception('YNAB API error')
        
        result = lambda_handler({}, None)
        
        assert result['statusCode'] == 500
        assert 'Failed to update YNAB' in json.loads(result['body'])['error']

def test_send_notification():
    """Test sending SNS notification."""
    with patch('boto3.client') as mock_boto3, \
         patch.dict(os.environ, {'NOTIFICATION_TOPIC_ARN': 'test-topic-arn'}):
        mock_sns = MagicMock()
        mock_boto3.return_value = mock_sns
        
        send_notification('Test Subject', 'Test Message')
        
        mock_sns.publish.assert_called_once_with(
            TopicArn='test-topic-arn',
            Subject='Test Subject',
            Message='Test Message'
        )

def test_get_update_mode_monthly():
    """Test getting monthly update mode from SSM."""
    get_update_mode.cache_clear()  # Clear the cache before test
    with patch('src.lambda_function.get_ssm_parameter') as mock_ssm:
        mock_ssm.return_value = UpdateMode.MONTHLY.value
        mode = get_update_mode()
        assert mode == UpdateMode.MONTHLY
        mock_ssm.assert_called_once_with('/ynab/update_mode')

def test_get_update_mode_yearly():
    """Test getting yearly update mode from SSM."""
    get_update_mode.cache_clear()  # Clear the cache before test
    with patch('src.lambda_function.get_ssm_parameter') as mock_ssm:
        mock_ssm.return_value = UpdateMode.YEARLY.value
        mode = get_update_mode()
        assert mode == UpdateMode.YEARLY
        mock_ssm.assert_called_once_with('/ynab/update_mode')

def test_get_update_mode_default():
    """Test default update mode when parameter is missing."""
    get_update_mode.cache_clear()  # Clear the cache before test
    with patch('src.lambda_function.get_ssm_parameter') as mock_ssm:
        mock_ssm.side_effect = Exception('Parameter not found')
        mode = get_update_mode()
        assert mode == UpdateMode.MONTHLY

def test_get_yearly_ipc_rate():
    """Test getting yearly IPC rate."""
    with patch('requests.get') as mock_get:
        # Mock response with 13 months of data
        mock_get.return_value.json.return_value = {
            "Data": [
                {"Valor": "103.2", "Fecha": "2024-03-01T00:00:00"},  # Current month
                {"Valor": "103.0", "Fecha": "2024-02-01T00:00:00"},
                {"Valor": "102.8", "Fecha": "2024-01-01T00:00:00"},
                {"Valor": "102.5", "Fecha": "2023-12-01T00:00:00"},  # Last December
                {"Valor": "102.0", "Fecha": "2023-11-01T00:00:00"}
            ]
        }
        
        result = get_yearly_ipc_rate()
        assert result["rate"] == pytest.approx(0.6829, rel=1e-4)  # (103.2 - 102.5) / 102.5 * 100
        assert result["date"] == "2024"
        assert result["mode"] == "yearly"

def test_get_yearly_ipc_rate_no_december():
    """Test error when December data is not available."""
    with patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {
            "Data": [
                {"Valor": "103.2", "Fecha": "2024-03-01T00:00:00"},
                {"Valor": "103.0", "Fecha": "2024-02-01T00:00:00"}
            ]
        }
        
        with pytest.raises(ValueError, match="Could not find last December's IPC value"):
            get_yearly_ipc_rate()

def test_format_ipc_message_monthly():
    """Test formatting monthly IPC message."""
    result = format_ipc_message(1004000, 1006000, 0.2, '2025-01', 'monthly')
    assert result == '2025-01 Monthly IPC: 0.20%: 1004.00€ -> 1006.00€'

def test_format_ipc_message_yearly():
    """Test formatting yearly IPC message."""
    result = format_ipc_message(1004000, 1024000, 2.0, '2025', 'yearly')
    assert result == '2025 Annual IPC: 2.00%: 1004.00€ -> 1024.00€'

def test_lambda_handler_yearly_wrong_month():
    """Test yearly update attempted in wrong month."""
    with patch('src.lambda_function.get_update_mode') as mock_mode, \
         patch('src.lambda_function.datetime') as mock_datetime:
        
        # Mock update mode as yearly
        mock_mode.return_value = UpdateMode.YEARLY
        
        # Mock current date as March
        mock_date = MagicMock()
        mock_date.month = 3
        mock_date.isoformat.return_value = '2024-03-20'
        mock_datetime.now.return_value = mock_date
        
        result = lambda_handler({}, None)
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'Skipped: Yearly updates only run in January' in body['message']
        assert body['update_mode'] == 'yearly'
        assert body['current_month'] == 3

def test_lambda_handler_yearly_correct_month():
    """Test yearly update in January."""
    with patch('src.lambda_function.get_update_mode') as mock_mode, \
         patch('src.lambda_function.datetime') as mock_datetime, \
         patch('src.lambda_function.get_ipc_rate') as mock_ipc, \
         patch('src.lambda_function.update_ynab_targets') as mock_update:
        
        # Mock update mode as yearly
        mock_mode.return_value = UpdateMode.YEARLY
        
        # Mock current date as January
        mock_date = MagicMock()
        mock_date.month = 1
        mock_date.isoformat.return_value = '2024-01-20'
        mock_datetime.now.return_value = mock_date
        
        # Mock IPC rate and update response
        mock_ipc.return_value = {'rate': 3.5, 'date': '2024', 'mode': 'yearly'}
        mock_update.return_value = {
            'results': [
                {
                    'category_name': 'Test Category',
                    'status': 'updated',
                    'old_target': 1000000,
                    'new_target': 1035000
                }
            ]
        }
        
        result = lambda_handler({}, None)
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['monthly_rate'] == 3.5
        assert body['period'] == '2024'
        assert len(body['results']) == 1
        assert body['results'][0]['status'] == 'updated'
        assert body['update_mode'] == 'yearly'
        assert body['current_month'] == 1