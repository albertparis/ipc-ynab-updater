import json
import pytest
from unittest.mock import patch, MagicMock
from src.lambda_function import (
    get_ipc_rate,
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
    result = format_ipc_message(1004000, 1006000, 0.2, '2025-01')
    assert result == '2025-01 IPC: 0.2%: 1004.00€ -> 1006.00€'

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
            {'date': '2025-01', 'rate': 0.2}
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
            {'date': '2025-01', 'rate': 0.616}  # This will result in 1006.16€, rounded to 1006.00€
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
        mock_get.return_value.json.side_effect = [
            {'data': {'category': {'goal_target': 1004000, 'name': 'Category 1', 'note': ''}}},
            {'data': {'category': {'goal_target': 2000000, 'name': 'Category 2', 'note': ''}}}
        ]
        
        # Mock update responses
        mock_patch.return_value.json.return_value = {'data': {'category': {'id': 'category_id'}}}
        
        result = update_ynab_targets({'date': '2025-01', 'rate': 0.2})
        
        assert len(result['results']) == 2
        assert result['results'][0]['category_name'] == 'Category 1'
        assert result['results'][0]['old_target'] == 1004000  # Keep in millicents
        assert result['results'][0]['new_target'] == 1006000  # 1004.00 * 1.002 = 1006.008, rounded to 1006.00
        assert result['results'][1]['category_name'] == 'Category 2'
        assert result['results'][1]['old_target'] == 2000000  # Keep in millicents
        assert result['results'][1]['new_target'] == 2004000  # 2000.00 * 1.002 = 2004.00
        
        # Verify notification was sent with correct euro amounts
        mock_notify.assert_called_once()
        notification_message = mock_notify.call_args[1]['message']
        assert 'Category 1: 1004.00€ -> 1006.00€' in notification_message
        assert 'Category 2: 2000.00€ -> 2004.00€' in notification_message

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