#!/bin/bash
# ec2-scheduler-setup.sh
#
# Creates EventBridge and Lambda rules that start and stop an EC2 instance on a
# daily schedule, so a pilot instance is not billed overnight.
#
# Review every value before running this — it creates real IAM, Lambda, and
# EventBridge resources in the target account.
#
# Schedule (Eastern Time / EDT = UTC-4):
#   Start:     Daily at 8:00 AM EDT  → 12:00 UTC
#   Stop:      Daily at 11:00 PM EDT → 03:00 UTC (next calendar day in UTC)
#
# Usage:
#   ./scripts/ec2-scheduler-setup.sh <instance-id> [region]
#
# Example:
#   ./scripts/ec2-scheduler-setup.sh i-0abc123def456789 us-east-1
#
# Prerequisites:
#   - AWS CLI installed and configured (aws configure)
#   - IAM permissions: lambda:*, iam:*, events:*, ec2:Describe*

set -e  # Exit immediately if any command fails

# ── Arguments ──────────────────────────────────────────────────────────────────
INSTANCE_ID="${1:?Error: instance ID required. Usage: $0 <instance-id> [region]}"
REGION="${2:-us-east-1}"

LAMBDA_ROLE_NAME="ec2-scheduler-lambda-role"
LAMBDA_FUNCTION_NAME="ec2-scheduler"

# ── Resolve AWS account ID ──────────────────────────────────────────────────────
echo "Resolving AWS account..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account: $ACCOUNT_ID | Region: $REGION | Instance: $INSTANCE_ID"

# ── Step 1: IAM trust policy (lets Lambda assume this role) ────────────────────
# Think of this as: "Lambda is allowed to wear this role's hat"
cat > /tmp/trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

echo ""
echo "Step 1/5: Creating IAM role..."
aws iam create-role \
  --role-name "$LAMBDA_ROLE_NAME" \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --region "$REGION" 2>/dev/null && echo "  Role created." || echo "  Role already exists, skipping."

# Attach the AWS-managed policy that lets Lambda write logs to CloudWatch
aws iam attach-role-policy \
  --role-name "$LAMBDA_ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Attach an inline policy scoped to only this specific EC2 instance
cat > /tmp/ec2-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ec2:StartInstances",
      "ec2:StopInstances"
    ],
    "Resource": "arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}"
  }]
}
EOF

aws iam put-role-policy \
  --role-name "$LAMBDA_ROLE_NAME" \
  --policy-name "ec2-scheduler-policy" \
  --policy-document file:///tmp/ec2-policy.json
echo "  IAM policies attached."

# ── Step 2: Lambda function code ───────────────────────────────────────────────
# The function reads 'action' from the EventBridge event payload and calls
# the matching EC2 API. INSTANCE_ID is injected via environment variable.
cat > /tmp/lambda_function.py << 'PYEOF'
import boto3
import os

def handler(event, context):
    action = event['action']
    instance_id = os.environ['INSTANCE_ID']
    # boto3 auto-detects the region from the Lambda execution environment
    ec2 = boto3.client('ec2')

    if action == 'start':
        ec2.start_instances(InstanceIds=[instance_id])
        print(f"Started {instance_id}")
    elif action == 'stop':
        ec2.stop_instances(InstanceIds=[instance_id])
        print(f"Stopped {instance_id}")
    else:
        raise ValueError(f"Unknown action: {action}")
PYEOF

cd /tmp && zip -q lambda.zip lambda_function.py
echo ""
echo "Step 2/5: Lambda code packaged."

# ── Step 3: Create Lambda function ────────────────────────────────────────────
# IAM roles take ~10s to propagate globally — Lambda creation will fail
# if we try too soon after creating the role.
echo ""
echo "Step 3/5: Creating Lambda function (waiting 10s for IAM propagation)..."
sleep 10

ROLE_ARN=$(aws iam get-role --role-name "$LAMBDA_ROLE_NAME" --query Role.Arn --output text)

aws lambda create-function \
  --function-name "$LAMBDA_FUNCTION_NAME" \
  --runtime python3.12 \
  --role "$ROLE_ARN" \
  --handler lambda_function.handler \
  --zip-file fileb:///tmp/lambda.zip \
  --environment "Variables={INSTANCE_ID=$INSTANCE_ID}" \
  --region "$REGION" 2>/dev/null && echo "  Lambda created." || {
    echo "  Lambda exists, updating code..."
    aws lambda update-function-code \
      --function-name "$LAMBDA_FUNCTION_NAME" \
      --zip-file fileb:///tmp/lambda.zip \
      --region "$REGION" > /dev/null
}

LAMBDA_ARN=$(aws lambda get-function \
  --function-name "$LAMBDA_FUNCTION_NAME" \
  --region "$REGION" \
  --query Configuration.FunctionArn \
  --output text)

# ── Step 4: EventBridge rules ──────────────────────────────────────────────────
# EventBridge cron format: cron(minutes hours day-of-month month day-of-week year)
# Use ? for day-of-week when specifying day-of-month (AWS requirement)
echo ""
echo "Step 4/5: Creating EventBridge rules..."

create_rule() {
  local rule_name="$1"
  local schedule="$2"
  local action="$3"

  aws events put-rule \
    --name "$rule_name" \
    --schedule-expression "$schedule" \
    --state ENABLED \
    --region "$REGION" > /dev/null

  # Grant EventBridge permission to invoke our Lambda
  aws lambda add-permission \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --statement-id "$rule_name" \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${rule_name}" \
    --region "$REGION" 2>/dev/null || true  # Ignore if permission already exists

  # Wire the rule to the Lambda, passing {"action": "<action>"} as the event
  aws events put-targets \
    --rule "$rule_name" \
    --targets "[{\"Id\":\"1\",\"Arn\":\"${LAMBDA_ARN}\",\"Input\":\"{\\\"action\\\":\\\"${action}\\\"}\"}]" \
    --region "$REGION" > /dev/null

  echo "  [$rule_name] → action=$action | schedule=$schedule"
}

# Daily start: 8:00 AM EDT = 12:00 UTC, every day
create_rule "ec2-daily-start"     "cron(0 12 * * ? *)"       "start"

# Daily stop: 11:00 PM EDT = 03:00 UTC (next day in UTC), every day
create_rule "ec2-daily-stop"      "cron(0 3 * * ? *)"        "stop"

# ── Step 5: Summary ────────────────────────────────────────────────────────────
echo ""
echo "Step 5/5: Cleaning up temp files..."
rm -f /tmp/trust-policy.json /tmp/ec2-policy.json /tmp/lambda_function.py /tmp/lambda.zip

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " EC2 Scheduler — Setup Complete"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Instance:  $INSTANCE_ID"
echo " Region:    $REGION"
echo ""
echo " Start:     Daily      8:00 AM EDT  (12:00 UTC)"
echo " Stop:      Daily     11:00 PM EDT  (03:00 UTC)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Resources created:"
echo "  IAM Role:        $LAMBDA_ROLE_NAME"
echo "  Lambda Function: $LAMBDA_FUNCTION_NAME"
echo "  EventBridge:     ec2-daily-start, ec2-daily-stop"
echo ""
echo "To verify, run:"
echo "  aws events list-rules --region $REGION --query 'Rules[?contains(Name, \`ec2\`)].{Name:Name,Schedule:ScheduleExpression}'"
