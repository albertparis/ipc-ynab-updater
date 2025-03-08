# IPC to YNAB Lambda Function

This AWS Lambda function automatically updates multiple YNAB category targets based on the IPC (Consumer Price Index) rate from the INE (Spanish National Statistics Institute). It supports both monthly and yearly update modes.

## Features

- Fetches IPC rate from INE API with two update modes:
  - Monthly: Uses the latest monthly rate
  - Yearly: Compares current value with last December's value
- Updates multiple YNAB category targets with the new rate
- Maintains a history of updates in category notes
- Sends detailed email notifications for successful updates and skips
- Runs automatically on the 20th of each month
- Secure credential management using AWS Systems Manager Parameter Store
- Comprehensive test coverage
- CI/CD pipeline with GitHub Actions

## Prerequisites

- AWS Account with appropriate permissions
- YNAB account with API access
- GitHub account for CI/CD
- Email address for notifications

## Setup

1. **YNAB API Setup**
   - Get your YNAB API token:
     - Go to [YNAB Developer Settings](https://app.ynab.com/settings/developer)
     - Click "New Token"
     - Copy the token and store it securely
   
   - Get your Budget ID:
     ```bash
     curl -H "Authorization: Bearer YOUR_YNAB_TOKEN" https://api.ynab.com/v1/budgets | jq '.data.budgets[] | select(.name=="YOUR_BUDGET_NAME") | .id'
     ```
     This will output your budget ID like: `"123e4567-e89b-12d3-a456-426614174000"`
   
   - Get Category IDs:
     ```bash
     curl -H "Authorization: Bearer YOUR_YNAB_TOKEN" https://api.ynab.com/v1/budgets/YOUR_BUDGET_ID/categories | jq '.data.category_groups[].categories[] | select(.name=="YOUR_CATEGORY_NAME") | .id'
     ```
     This will output the category ID like: `"4b1e98a1-e90d-45f0-8d8e-44ffbc54aff8"`

2. **AWS Systems Manager Parameter Store**
   Create the following parameters in AWS Systems Manager Parameter Store:
   - `/ynab/token`: Your YNAB API token
   - `/ynab/budget_id`: Your YNAB budget ID
   - `/ynab/category_ids`: Comma-separated list of category IDs to update (e.g., "category1,category2,category3")
   - `/ynab/update_mode`: Update mode, either "monthly" or "yearly" (defaults to "monthly" if not set)

3. **Local Development**
   ```bash
   # Create and activate virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

   # Install dependencies
   pip install -r requirements.txt

   # Run tests
   pytest tests/
   ```

4. **AWS Deployment**
   ```bash
   # Install AWS SAM CLI
   brew install aws-sam-cli  # On macOS

   # Build and deploy
   cd infrastructure
   sam build
   sam deploy --guided --parameter-overrides EmailNotification=your.email@example.com
   ```

5. **GitHub Actions Setup**
   - Fork this repository
   - Add the following secrets to your GitHub repository:
     - `AWS_ACCESS_KEY_ID`
     - `AWS_SECRET_ACCESS_KEY`
     - `NOTIFICATION_EMAIL`: Email address for notifications

## How It Works

1. **Update Mode**
   The function supports two update modes:
   - **Monthly Mode** (default):
     - Uses the latest monthly IPC rate
     - Updates category targets monthly
     - Note format: "2024-03 Monthly IPC: 0.3%: 1000.00€ -> 1003.00€"
   
   - **Yearly Mode**:
     - Compares current value with last December's value
     - Still runs monthly but uses year-over-year rate
     - Note format: "2024 Annual IPC: 3.5%: 1000.00€ -> 1035.00€"

2. **IPC Rate Fetching**
   - Monthly Mode:
     - Gets the latest monthly rate from INE's API
     - Typically published around the 14th of each month
   
   - Yearly Mode:
     - Gets the last 13 months of data to ensure December's value is available
     - Calculates year-over-year change: ((current - lastDecember) / lastDecember) * 100

3. **Category Updates**
   - Retrieves the list of category IDs from SSM Parameter Store
   - For each category:
     - Gets current target amount and notes
     - Checks if an update is needed for the current period
     - Calculates new target based on IPC rate
     - Updates the target and prepends update history to notes

4. **Amount Handling**
   - YNAB stores amounts in millicents (1/1000 of a euro)
   - The function rounds new amounts to the nearest euro (1000 millicents)
   - Messages display amounts with two decimal places (e.g., "1004.00€ -> 1006.00€")

5. **Notifications**
   Sends an email with:
   - Period and IPC rate
   - List of updated categories with old and new amounts
   - List of skipped categories (if already updated)
   - Any errors that occurred

## Configuration

The Lambda function is configured to:
- Run at midnight (UTC) on the 20th of each month (as INE typically publishes the IPC update around the 14th)
- Use 128MB of memory
- Timeout after 30 seconds
- Use Python 3.11 runtime

## CloudWatch Alarms

The function includes two CloudWatch alarms:
- Error alarm: Triggers if any errors occur during execution
- Duration alarm: Triggers if execution takes longer than expected

## Testing

```bash
# Run tests with coverage
pytest tests/ --cov=src --cov-report=xml

# Run specific test file
pytest tests/test_lambda_function.py

# Run a specific test
pytest tests/test_lambda_function.py -k test_update_ynab_targets
```

## Monitoring

- CloudWatch Logs: View function logs and execution details
- CloudWatch Metrics: Monitor function performance and errors
- SNS Notifications: Receive email updates about function execution
- GitHub Actions: View CI/CD pipeline status and test results

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

MIT License - see LICENSE file for details