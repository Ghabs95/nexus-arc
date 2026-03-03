#!/usr/bin/env bash

# Nexus Webhook Server Setup Script
# This script helps you install and configure the GitHub webhook server

set -e  # Exit on error

echo "🚀 Nexus Webhook Server Setup"
echo "=============================="
echo ""

# Check if running as root (shouldn't be for systemd --user)
if [[ $EUID -eq 0 ]]; then
   echo "⚠️  This script should be run as the ubuntu user, not root"
   echo "   Run: sudo -u ubuntu bash $0"
   exit 1
fi

NEXUS_DIR="/opt/nexus-arc/examples/nexus-bot"
NEXUS_ENV_FILE="./.env"
SERVICE_FILE="nexus-webhook.service"

if [[ ! -f "$NEXUS_ENV_FILE" ]]; then
    echo "Missing $NEXUS_ENV_FILE"
    echo "Create it from .env.example first"
    exit 1
fi

DEPLOY_TYPE=$(grep -E '^DEPLOY_TYPE=' "$NEXUS_ENV_FILE" | cut -d= -f2- | tr -d '[:space:]')
DEPLOY_TYPE=${DEPLOY_TYPE:-compose}

if [[ "$DEPLOY_TYPE" != "systemd" ]]; then
    echo "This script is only for systemd deployments (DEPLOY_TYPE=systemd)."
    echo "Current DEPLOY_TYPE=$DEPLOY_TYPE"
    echo "For compose deployments use: ./scripts/deploy.sh up"
    exit 0
fi

# Step 1: Generate webhook secret if not exists
echo "📝 Step 1: Webhook Secret"
echo "-------------------------"

if grep -q "^WEBHOOK_SECRET=$" "$NEXUS_ENV_FILE"; then
    echo "No webhook secret found. Generating one..."
    WEBHOOK_SECRET=$(openssl rand -hex 32)
    sed -i "s|^WEBHOOK_SECRET=$|WEBHOOK_SECRET=$WEBHOOK_SECRET|" "$NEXUS_ENV_FILE"
    echo "✅ Generated webhook secret: $WEBHOOK_SECRET"
    echo ""
    echo "⚠️  IMPORTANT: Save this secret! You'll need it for GitHub webhook configuration."
    echo "   Add it to your repository webhook settings page"
    echo ""
else
    WEBHOOK_SECRET=$(grep "^WEBHOOK_SECRET=" "$NEXUS_ENV_FILE" | cut -d= -f2)
    if [[ -z "$WEBHOOK_SECRET" ]]; then
        echo "⚠️  Warning: WEBHOOK_SECRET is empty in $NEXUS_ENV_FILE"
        echo "   Generating a new secret..."
        WEBHOOK_SECRET=$(openssl rand -hex 32)
        sed -i "s|^WEBHOOK_SECRET=.*|WEBHOOK_SECRET=$WEBHOOK_SECRET|" "$NEXUS_ENV_FILE"
        echo "✅ Generated webhook secret: $WEBHOOK_SECRET"
    else
        echo "✅ Webhook secret already configured"
    fi
fi

# Step 2: Install Flask (if needed)
echo ""
echo "📦 Step 2: Dependencies"
echo "----------------------"
if "$NEXUS_DIR/venv/bin/pip" show flask &>/dev/null; then
    echo "✅ Flask already installed"
else
    echo "Installing Flask..."
    "$NEXUS_DIR/venv/bin/pip" install flask
    echo "✅ Flask installed"
fi

# Step 3: Install systemd service
echo ""
echo "⚙️  Step 3: Systemd Service"
echo "--------------------------"

sudo cp "$NEXUS_DIR/$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
echo "✅ Service file installed"

# Step 4: Enable and start service
echo ""
echo "🔄 Step 4: Start Service"
echo "-----------------------"

read -p "Start the webhook server now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo systemctl enable nexus-webhook
    sudo systemctl start nexus-webhook
    
    sleep 2
    if sudo systemctl is-active --quiet nexus-webhook; then
        echo "✅ Webhook server is running!"
        sudo systemctl status nexus-webhook --no-pager -l
    else
        echo "❌ Webhook server failed to start. Check logs:"
        echo "   sudo journalctl -u nexus-webhook -n 50"
        exit 1
    fi
else
    echo "⏭️  Skipped starting service"
    echo "   To start later: sudo systemctl start nexus-webhook"
fi

# Step 5: Test webhook endpoint
echo ""
echo "🧪 Step 5: Test Endpoint"
echo "-----------------------"

WEBHOOK_PORT=$(grep "^WEBHOOK_PORT=" "$NEXUS_ENV_FILE" | cut -d= -f2)
WEBHOOK_PORT=${WEBHOOK_PORT:-8081}

sleep 1
if curl -s "http://localhost:$WEBHOOK_PORT/health" | grep -q "healthy"; then
    echo "✅ Webhook server is responding on port $WEBHOOK_PORT"
else
    echo "⚠️  Webhook server health check failed"
    echo "   Check if port $WEBHOOK_PORT is accessible"
fi

# Step 6: GitHub webhook configuration instructions
echo ""
echo "🔧 Step 6: GitHub Configuration"
echo "-------------------------------"
echo ""
echo "Configure a webhook in your repository settings"
echo "(Settings → Webhooks → Add webhook)"
echo ""
echo "Settings:"
echo "  • Payload URL: http://<your-server-ip>:$WEBHOOK_PORT/webhook"
echo "  • Content type: application/json"
echo "  • Secret: $WEBHOOK_SECRET"
echo "  • Events: Choose individual events:"
echo "    ✓ Issues (for automatic triage of new issues)"
echo "    ✓ Issue comments (for workflow completion detection)"
echo "    ✓ Pull requests"
echo "    ✓ Pull request reviews"
echo ""

# Step 7: Optional - nginx reverse proxy
echo "💡 Optional: Nginx Reverse Proxy"
echo "--------------------------------"
echo ""
echo "For production, consider setting up nginx reverse proxy with SSL:"
echo ""
echo "1. Install nginx: sudo apt install nginx"
echo "2. Configure reverse proxy (see docs/WEBHOOK-REFERENCE.md → Production Deployment)"
echo "3. Set up SSL with Let's Encrypt"
echo "4. Update GitHub webhook URL to use https://<domain>/webhook"
echo ""

# Summary
echo "✅ Setup Complete!"
echo ""
echo "Next steps:"
echo "  1. Configure GitHub webhook (see instructions above)"
echo "  2. Test with a comment on an issue"
echo "  3. Monitor logs: tail -f $NEXUS_DIR/logs/webhook.log"
echo "  4. Check service: sudo systemctl status nexus-webhook"
echo ""
echo "For more details, see docs/WEBHOOK-REFERENCE.md or docs/WEBHOOK-QUICKSTART.md"
