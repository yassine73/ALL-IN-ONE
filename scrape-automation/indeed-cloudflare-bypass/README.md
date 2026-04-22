# Indeed Cloudflare Bypass

This Python script is designed to bypass Cloudflare protection and extract resume content from an Indeed profile. It uses `undetected-chromedriver` to mimic a real user and maintain a persistent session.

## 🚀 Features

- **Cloudflare Bypass**: Utilizes `undetected-chromedriver` to avoid bot detection.
- **Session Persistence**: Saves browser profile data to a local directory, avoiding the need to log in every time.
- **Manual Login Mode**: Provides a dedicated mode to handle the initial authentication and Cloudflare challenges manually.

## 🛠️ Prerequisites

You will need:
- Python 3.x
- Google Chrome installed on your system.
- The following Python libraries:
  ```bash
  pip install undetected-chromedriver selenium
  ```

## 📖 Usage

### 1. Initial Setup & Login
Since Indeed has aggressive bot detection, you must first log in manually to establish a valid session.

Run the script with the `--login` flag:
```bash
python3 indeed-cloudflare-bypass.py --login
```
- A Chrome window will open.
- Log into your Indeed account.
- Once you have successfully reached your resume page and confirmed you are logged in, **close the browser window**.
- The session will be saved in the `../session` directory relative to the script.

### 2. Extracting the Resume
Once the session is saved, you can run the script normally to extract the resume text without manual intervention:
```bash
python3 indeed-cloudflare-bypass.py
```

## ⚠️ Troubleshooting

- **Detection**: If you see "Attention Required!" or a login screen during the extraction phase, your session may have expired or the bot was detected. 
- **Fix**: Re-run the script in login mode: `python3 indeed-cloudflare-bypass.py --login`.
- **Chrome Version**: Ensure your Chrome browser is up to date, as `undetected-chromedriver` depends on the installed version.

## 📂 Project Structure
- `indeed-cloudflare-bypass.py`: The main execution script.
- `../session/`: (Created automatically) Stores the Chrome user profile and cookies.
