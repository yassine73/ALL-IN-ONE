import os
import sys
import time
import random
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def get_resume():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    user_data_dir = os.path.join(base_dir, "..", "session")
    url = "https://profile.indeed.com/resume"

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Attempting headless mode first. 
    # undetected-chromedriver is better, but Indeed is very aggressive.
    # options.add_argument("--headless") 
    
    try:
        print("🚀 Starting stealth browser...")
        driver = uc.Chrome(options=options)
        
        print(f"Navigating to {url}...")
        driver.get(url)

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".css-1in7vwb.ecydgvn0"))
        )
        
        page_source = driver.page_source
        current_url = driver.current_url
        
        if "login" in current_url or "Sign in" in page_source or "Attention Required!" in page_source:
            print("\n❌ [Detection] Cloudflare challenge or Login screen detected.")
            print("Headless mode is likely being detected or the session expired.")
            print("Fix: Run 'python3 <script> --login' to refresh the session in headed mode.")
            driver.quit()
            sys.exit(1)
            
        print("Extracting resume content...")
        time.sleep(random.uniform(1, 3))
        
        # Extract text from the body
        resume_text = driver.find_element(By.TAG_NAME, "body").text
        
        print("\n--- RESUME CONTENT START ---")
        print(resume_text)
        print("--- RESUME CONTENT END ---")
        
        driver.quit()
        
    except Exception as e:
        print(f"An error occurred: {e}")
        if 'driver' in locals():
            driver.quit()

def login_mode():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    user_data_dir = os.path.join(base_dir, "..", "session")
    url = "https://profile.indeed.com/resume"

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    # Headless MUST be False for login
    
    try:
        print("🚀 Opening browser for manual login. Please log into Indeed.")
        driver = uc.Chrome(options=options)
        driver.get(url)
        
        print("Waiting for you to log in... Once you see your resume, close the browser window.")
        
        # Keep script alive until browser is closed
        while True:
            try:
                _ = driver.window_handles
                time.sleep(1)
            except Exception:
                print("Browser closed. Session saved.")
                break
        driver.quit()
    except Exception as e:
        print(f"An error occurred during login: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--login":
        login_mode()
    else:
        get_resume()
