import os
import json
import time
import urllib.request
import urllib.error
from bs4 import BeautifulSoup
import re

# Base URLs
WIKI_BASE = "https://path-of-titans.fandom.com"
CARNIVORES_URL = f"{WIKI_BASE}/wiki/Carnivores"
HERBIVORES_URL = f"{WIKI_BASE}/wiki/Herbivores"
MODDED_URL = f"{WIKI_BASE}/wiki/Category:Modded_Dinosaurs"
STATS_URL = f"{WIKI_BASE}/wiki/Dinosaur_Stats"

# Output directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets", "dinos")
os.makedirs(ASSETS_DIR, exist_ok=True)
JSON_PATH = os.path.join(BASE_DIR, "dinos.json")

# User agent (Fandom blocks empty/python UAs)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5'
}

def fetch_html(url):
    print(f"Fetching {url}...")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req) as response:
            return response.read().decode('utf-8')
    except urllib.error.URLError as e:
        print(f"Error fetching {url}: {e}")
        return None

def download_image(url, filepath):
    if os.path.exists(filepath):
        return True # Skip if we already have it
    print(f"Downloading image {url} to {filepath}...")
    # Sometimes fandom urls have extra /revision/latest?cb=... need to clean or just download as is
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req) as response:
            with open(filepath, 'wb') as f:
                f.write(response.read())
        return True
    except urllib.error.URLError as e:
        print(f"Error downloading {url}: {e}")
        return False

def extract_dinos_from_category(url, diet="unknown"):
    html = fetch_html(url)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    dinos = []
    
    # Fandom galleries
    for item in soup.select('.category-page__member-link'):
        name = item.text.strip()
        link = WIKI_BASE + item['href']
        if "Category:" in name:
            continue
        dinos.append({"name": name, "url": link, "diet": diet})
        
    return dinos

def extract_dinos_from_gallery(url, diet):
    html = fetch_html(url)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    dinos = []
    
    # Looking for a gallery on Carnivores / Herbivores pages
    for item in soup.select('.gallery-image-wrapper'):
        parent = item.find_parent('div', class_='wikia-gallery-item')
        if parent:
            caption = parent.select_one('.lightbox-caption')
            if caption and caption.find('a'):
                a_tag = caption.find('a')
                name = a_tag.text.strip()
                link = WIKI_BASE + a_tag['href']
                
                # Try to get image url from wrapper
                img = item.find('img')
                img_url = None
                if img:
                    img_url = img.get('data-src') or img.get('src')
                    if img_url and 'scale-to-width-down' in img_url:
                        # try to get original
                        img_url = img_url.split('/revision/')[0]
                
                dinos.append({"name": name, "url": link, "diet": diet, "img_url_hint": img_url})

    # Fandom categories fallback if gallery not found
    if not dinos:
        for item in soup.select('li > a[title]'):
            title = item.get('title', '')
            if not title.startswith("User:") and not title.startswith("Category:"):
                nav = item.find_parent('nav')
                if not nav and 'class' not in item.attrs:
                     dinos.append({"name": title, "url": WIKI_BASE + item['href'], "diet": diet})
    
    return dinos

def parse_stats_page():
    html = fetch_html(STATS_URL)
    if not html: return {}
    
    soup = BeautifulSoup(html, 'html.parser')
    stats_dict = {}
    
    # Find all sortable tables
    tables = soup.select('table.sortable')
    for table in tables:
        rows = table.find_all('tr')[1:] # Skip header
        for row in rows:
            cols = row.find_all(['td', 'th'])
            if len(cols) >= 5:
                # Name usually first or second column with a link
                name_cell = cols[0]
                a_tag = name_cell.find('a')
                if a_tag:
                    name = a_tag.text.strip()
                else:
                    name = name_cell.text.strip()
                
                try:
                    cw = int(cols[1].text.strip().replace(',',''))
                except:
                    cw = 3000
                try:
                    hp = int(cols[2].text.strip().replace(',',''))
                except:
                    hp = 500
                try:
                    spd = int(cols[3].text.strip().replace(',',''))
                except:
                    spd = 500
                
                # ATK and Armor might not be cleanly parsed from this page, provide defaults or parse if available
                # Often armor isn't in the global table directly
                stats_dict[name.lower()] = {
                    "cw": cw,
                    "hp": hp,
                    "spd": spd
                }
    return stats_dict

def scrape_dino_profile(dino):
    html = fetch_html(dino['url'])
    if not html: return
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # Try to find a good image (usually in portable infobox)
    img_tag = soup.select_one('.pi-image-collection img') or soup.select_one('.pi-image img')
    img_url = None
    if img_tag:
        img_url = img_tag.get('src') or img_tag.get('data-src')
        if img_url:
            img_url = img_url.split('/revision/')[0] # Get full res
    elif dino.get("img_url_hint"):
        img_url = dino["img_url_hint"]
        
    dino['img_url'] = img_url
    
    # Get ID
    dino_id = re.sub(r'[^a-z0-9]', '_', dino['name'].lower())
    dino_id = re.sub(r'_+', '_', dino_id).strip('_')
    dino['id'] = dino_id
    
    # If image URL exists, download it
    if img_url:
        filepath = os.path.join(ASSETS_DIR, f"{dino_id}.png")
        if not download_image(img_url, filepath):
             # Try replacing .webp or .jpg extension logic if Fandom forces it, but Fandom usually serves webp unless asked
             # If extension is mismatch, PIL can still open it usually if we just save the bytes.
             pass

def main():
    print("Starting PoT Wiki Scraper...")
    all_dinos = []
    
    # 1. Scrape Carnivores
    print("--- Scraping Carnivores ---")
    carns = extract_dinos_from_gallery(CARNIVORES_URL, "carnivore")
    # If gallery strategy fails, try category direct links (some wikis organize differently)
    
    # 2. Scrape Herbivores
    print("--- Scraping Herbivores ---")
    herbs = extract_dinos_from_gallery(HERBIVORES_URL, "herbivore")
    
    # 3. Scrape Modded via Category page
    print("--- Scraping Modded ---")
    modded = extract_dinos_from_category(MODDED_URL, "unknown")
    
    # Filter out non-dinosaur pages from category logic
    valid_dinos = []
    seen = set()
    for d in carns + herbs + modded:
        if d['name'] in seen:
            continue
        seen.add(d['name'])
        valid_dinos.append(d)
        
    print(f"Found {len(valid_dinos)} unique profiles.")
    
    # 4. Scrape Global Stats
    print("--- Scraping Global Stats Table ---")
    stats_map = parse_stats_page()
    
    # 5. Process Profiles & Download Images
    # Load existing to avoid overwrites
    existing = []
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except:
            pass
            
    existing_ids = {x.get('id', '') for x in existing}
    
    for i, d in enumerate(valid_dinos):
        print(f"[{i+1}/{len(valid_dinos)}] Processing {d['name']}...")
        scrape_dino_profile(d)
        time.sleep(1) # Be polite to fandom
        
    # 6. Merge formatting
    final_output = []
    # Add existing ones not scraped
    final_output.extend([e for e in existing if e.get('id') not in [d.get('id') for d in valid_dinos]])
    
    for d in valid_dinos:
        if not d.get('id'): continue
        name_key = d['name'].lower()
        stat_data = stats_map.get(name_key, {})
        
        # Determine ATK / Armor logic
        cw = stat_data.get('cw', 3000)
        hp = stat_data.get('hp', max(100, int(cw / 6.0)))
        spd = stat_data.get('spd', 500)
        
        # Derive ATK/Armor if missing (modded or unlisted)
        atk = max(10, int(cw / 60))
        armor = round(max(0.5, cw / 3000.0), 1)
        
        # If diet is unknown (modded), guess based on name or default
        diet = d['diet']
        if diet == "unknown":
            diet = "carnivore"
            
        entry = {
            "id": d['id'],
            "name": d['name'],
            "type": diet,
            "lore": f"A creature belonging to the {diet} family.",
            "cw": cw,
            "hp": hp,
            "atk": atk,
            "armor": armor,
            "spd": spd,
            "img_url": d.get("img_url", "") # Keeping for reference
        }
        final_output.append(entry)
        
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, indent=4)
        
    print(f"Successfully scraped and merged {len(final_output)} dinosaurs into dinos.json.")

if __name__ == "__main__":
    main()
