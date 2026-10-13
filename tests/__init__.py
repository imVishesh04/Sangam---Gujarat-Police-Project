# Test package setup.
# Never send real WhatsApp messages from the test suite, whatever .env says:
# set these BEFORE config.py loads .env (it does not override existing vars).
import os

os.environ["ALERT_AGENT_ENABLED"] = "0"
os.environ["WHATSAPP_PROVIDER"] = "log"
