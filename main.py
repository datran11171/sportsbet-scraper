from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service  # <-- Add this
import pandas as pd

driver_option = webdriver.ChromeOptions()
driver_option.add_argument("--incognito")  # Note: fixed the dash encoding here too

chromedriver_path = '/Users/JamieTran/Downloads/chromedriver-mac-arm64/chromedriver'  # <-- Define the path to chromedriver

def create_webdriver():
    service = Service(executable_path=chromedriver_path)  # <-- Wrap path in Service
    return webdriver.Chrome(service=service, options=driver_option)  # <-- Use `options` not `chrome_options`

browser = create_webdriver()
browser.get("https://github.com/collections/machine-learning")

# Extract all projects
projects = browser.find_elements(By.XPATH, "//h1[@class='h3 lh-condensed']")

# Extract information for each project
project_list = {}
for proj in projects:
    proj_name = proj.text
    anchors = proj.find_elements(By.XPATH, "a")
    proj_url = anchors[0].get_attribute('href') if anchors else None
    project_list[proj_name] = proj_url
    
    
# Extracting data
project_df = pd.DataFrame.from_dict(project_list, orient = 'index')

# Manipulate the table
project_df['project_name'] = project_df.index
project_df.columns = ['project_url', 'project_name']
project_df = project_df.reset_index(drop=True)

# Export project dataframe to CSV
project_df.to_csv('project_list.csv')