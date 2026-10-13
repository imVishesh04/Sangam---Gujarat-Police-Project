import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})

print("Launching headless Chrome...")
driver = webdriver.Chrome(options=chrome_options)

out_dir = r"C:\Users\imvis\.gemini\antigravity\brain\3ca74b00-b6bf-4dcd-9a9c-95ca502de073"
artifact_path = os.path.join(out_dir, "noc_system_health.png")
local_path = r"C:\GUJHACKATHON\GUJ HACKATHON - Copy\noc_system_health.png"

try:
    print("Navigating to http://localhost:5173...")
    driver.get("http://localhost:5173")
    time.sleep(2)

    # Click Demo Admin button if on LoginScreen
    print("Looking for Demo Admin button or login...")
    try:
        admin_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Demo Admin')]"))
        )
        print("Found Demo Admin button, clicking...")
        admin_btn.click()
        time.sleep(4)
    except Exception as e:
        print("No login button needed or error:", e)

    print("Checking browser logs:")
    for entry in driver.get_log('browser'):
        print(" [BROWSER LOG]", entry)

    # Click the "NOC & System Health" tab in the sidebar
    print("Navigating to NOC & System Health tab...")
    try:
        noc_tab = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'NOC & System Health') or contains(text(), 'NOC Health')]"))
        )
        noc_tab.click()
        print("Clicked NOC & System Health tab, waiting for telemetry to load...")
        time.sleep(3)
    except Exception as e:
        print("Could not find NOC tab directly:", e)

    driver.save_screenshot(artifact_path)
    driver.save_screenshot(local_path)
    print(f"Screenshot saved successfully to:\n  - {artifact_path}\n  - {local_path}")

finally:
    driver.quit()
