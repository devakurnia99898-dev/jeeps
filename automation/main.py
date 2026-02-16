import os
import json
import requests
import feedparser
import time
import re
import random
import warnings 
import string
from datetime import datetime
from slugify import slugify
from io import BytesIO
from PIL import Image
from groq import Groq, APIError, RateLimitError

# --- SUPPRESS WARNINGS ---
warnings.filterwarnings("ignore", category=FutureWarning)

# --- GOOGLE INDEXING LIBS ---
try:
    from oauth2client.service_account import ServiceAccountCredentials
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

# ==========================================
# ⚙️ CONFIGURATION & SETUP (VESLIFE / JEEP NICHE)
# ==========================================

GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "") 
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

WEBSITE_URL = "https://veslife.biz.id" 
INDEXNOW_KEY = "e74819b68a0f40e98f6ec3dc24f610f0" 
GOOGLE_JSON_KEY = os.environ.get("GOOGLE_INDEXING_KEY", "") 

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    exit(1)

# Penulis dengan Otoritas (E-E-A-T)
AUTHOR_PROFILES = [
    "Dave Harsya (Lead Off-road Specialist)", 
    "Sarah Jenkins (Automotive Engineer)",
    "Luca Romano (Classic Jeep Restorer)", 
    "Marcus Reynolds (4x4 Technical Analyst)",
    "Ben Foster (Overlanding Guide)"
]

# Kategori Spesifik
VALID_CATEGORIES = [
    "Wrangler & Gladiator", "Grand Cherokee", "Concept News", 
    "Off-Road Guides", "Technical Specs", "EV & Hybrid 4xe"
]

RSS_SOURCES = {
    "Autoblog Jeep": "https://www.autoblog.com/category/jeep/rss.xml",
    "Motor1 Jeep": "https://www.motor1.com/rss/make/jeep/",
    "Mopar Insiders": "https://moparinsiders.com/feed/", 
    "Jeep News": "https://www.autoevolution.com/rss/cars/jeep/"
}

CONTENT_DIR = "content/articles" 
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"

# 🔥 TARGET: 2 Artikel per sumber per run
TARGET_PER_SOURCE = 2

# ==========================================
# 🧠 HELPER FUNCTIONS
# ==========================================
def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(title, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    memory[title] = f"/articles/{slug}" 
    if len(memory) > 200: memory = dict(list(memory.items())[-200:])
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def get_internal_links_list():
    memory = load_link_memory()
    items = list(memory.items())
    if not items: return []
    count = min(3, len(items))
    return random.sample(items, count)

def fetch_rss_feed(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        return feedparser.parse(response.content) if response.status_code == 200 else None
    except: return None

def clean_ai_content(text):
    if not text: return ""
    text = re.sub(r'^```[a-zA-Z]*\n', '', text)
    text = re.sub(r'\n```$', '', text)
    text = text.replace("```", "")
    text = re.sub(r'^##\s*(Introduction|Conclusion|Summary|The Verdict|Final Thoughts|In Conclusion)\s*\n', '', text, flags=re.MULTILINE|re.IGNORECASE)
    text = text.replace("<h1>", "# ").replace("</h1>", "\n")
    text = text.replace("<h2>", "## ").replace("</h2>", "\n")
    text = text.replace("<h3>", "### ").replace("</h3>", "\n")
    text = text.replace("<h4>", "#### ").replace("</h4>", "\n")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<p>", "").replace("</p>", "\n\n")
    return text.strip()

def inject_links_into_body(content_body):
    links = get_internal_links_list()
    if not links: return content_body
    link_box = "\n\n> **🚙 Don't Miss:**\n"
    for title, url in links:
        link_box += f"> - [{title}]({url})\n"
    link_box += "\n"
    paragraphs = content_body.split('\n\n')
    if len(paragraphs) < 4: return content_body + link_box
    insert_pos = random.randint(1, 2) 
    paragraphs.insert(insert_pos, link_box)
    return "\n\n".join(paragraphs)

# ==========================================
# 🚀 INDEXING FUNCTIONS
# ==========================================
def submit_to_indexnow(url):
    try:
        endpoint = "https://api.indexnow.org/indexnow"
        host = WEBSITE_URL.replace("https://", "").replace("http://", "")
        data = {
            "host": host, "key": INDEXNOW_KEY,
            "keyLocation": f"https://{host}/{INDEXNOW_KEY}.txt",
            "urlList": [url]
        }
        requests.post(endpoint, json=data, headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=10)
        print(f"      🚀 IndexNow Submitted")
    except Exception as e: print(f"      ⚠️ IndexNow Failed: {e}")

def submit_to_google(url):
    if not GOOGLE_JSON_KEY or not GOOGLE_LIBS_AVAILABLE: return
    try:
        creds_dict = json.loads(GOOGLE_JSON_KEY)
        SCOPES = ["https://www.googleapis.com/auth/indexing"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
        service = build("indexing", "v3", credentials=credentials)
        body = {"url": url, "type": "URL_UPDATED"}
        service.urlNotifications().publish(body=body).execute()
        print(f"      🚀 Google Indexing Submitted")
    except Exception as e: print(f"      ⚠️ Google Indexing Error: {e}")

# ==========================================
# 🎨 JEEP-SPECIFIC IMAGE GENERATOR (FIXED)
# ==========================================
def generate_robust_image(prompt, filename):
    output_path = f"{IMAGE_DIR}/{filename}"
    
    # 1. BERSIHKAN PROMPT DARI KATA "SEDAN" / "BMW" DLL
    forbidden_words = ["sedan", "coupe", "bmw", "mercedes", "toyota", "low car", "sports car", "track car"]
    clean_prompt = prompt.lower().replace('"', '').replace("'", "")
    for word in forbidden_words:
        clean_prompt = clean_prompt.replace(word, "")
    
    # 2. PAKSA KATA KUNCI JEEP (INJECTION)
    # Ini memastikan apapun yang ditulis AI, output gambarnya tetap mobil tinggi/boxy
    forced_style = "Jeep Wrangler style SUV, rugged 4x4, boxy off-road vehicle, seven slot grille, lifted suspension, big tires, cinematic automotive photography, realistic 8k"
    
    final_prompt = f"{clean_prompt}, {forced_style}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://google.com"
    }

    print(f"      🎨 Generating Image: {clean_prompt[:30]}...")

    # STRATEGY 1: POLLINATIONS (Biasanya lebih patuh prompt mobil)
    try:
        seed = random.randint(1, 99999)
        # Model 'flux' biasanya sangat bagus untuk mobil realistis
        poly_url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(final_prompt)}?width=1280&height=720&model=flux&seed={seed}&nologo=true"
        resp = requests.get(poly_url, headers=headers, timeout=25)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(output_path, "WEBP", quality=90)
            print("      ✅ Image Saved (Source: Pollinations Flux)")
            return f"/images/{filename}"
    except Exception: pass

    # STRATEGY 2: HERCAI AI (Fallback)
    try:
        hercai_url = f"https://hercai.onrender.com/v3/text2image?prompt={requests.utils.quote(final_prompt)}"
        resp = requests.get(hercai_url, headers=headers, timeout=40)
        if resp.status_code == 200:
            data = resp.json()
            if "url" in data:
                img_data = requests.get(data["url"], headers=headers, timeout=20).content
                img = Image.open(BytesIO(img_data)).convert("RGB")
                img.save(output_path, "WEBP", quality=90)
                print("      ✅ Image Saved (Source: Hercai AI)")
                return f"/images/{filename}"
    except Exception: pass

    # STRATEGY 3: FLICKR FALLBACK (Safe Mode)
    # Kita cari 'wrangler' spesifik agar pasti Jeep
    try:
        flickr_url = f"https://loremflickr.com/1280/720/jeep,wrangler,rubicon/all"
        resp = requests.get(flickr_url, headers=headers, timeout=20, allow_redirects=True)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(output_path, "WEBP", quality=90)
            print("      ✅ Image Saved (Source: Real Photo Fallback)")
            return f"/images/{filename}"
    except Exception: pass

    return "/images/default-jeep.webp"

# ==========================================
# 🧠 CONTENT ENGINE (PRO WITH TABLES)
# ==========================================

def get_groq_article_json(title, summary, link, author_name):
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    structures = [
        "TECHNICAL_BREAKDOWN (Deep dive into specs, engine codes, suspension geometry)",
        "MARKET_IMPACT (How this affects values, competitors like Bronco/4Runner)",
        "ENTHUSIAST_PERSPECTIVE (Modding potential, trail capability focus)",
        "HISTORICAL_CONTEXT (Comparing to CJ/YJ/TJ/JK legacy)"
    ]
    chosen_structure = random.choice(structures)

    system_prompt = f"""
    You are {author_name}, a veteran automotive journalist and engineer.
    Current Date: {current_date}.
    
    OBJECTIVE: Write a high-quality, data-rich article about Jeep/Off-road.
    STRUCTURE STYLE: {chosen_structure}.
    
    🚫 NEGATIVE CONSTRAINTS:
    1. NEVER use headers named "Introduction", "Conclusion", "Summary".
    2. NEVER start with "In this article...".
    
    ✅ MANDATORY REQUIREMENTS:
    1. **DATA TABLE IS A MUST**: You MUST include a Markdown Table (Specs, Pros/Cons, or Key Facts).
    2. **VISUAL KEYWORD RULE**: For the 'main_keyword' field, you MUST describe a JEEP vehicle. Do not use generic words like "car" or "testing". Use specific terms like "Lifted Jeep Wrangler crawling rocks" or "Jeep Grand Cherokee interior".
    3. **HIERARCHY**: Use H2, H3, H4.
    
    OUTPUT FORMAT (JSON):
    {{
        "title": "Catchy SEO Title (No Clickbait)",
        "description": "Meta description (150 chars)",
        "category": "One of: {', '.join(VALID_CATEGORIES)}",
        "main_keyword": "Visual prompt description (e.g., 'Green Jeep Wrangler in mud')",
        "tags": ["tag1", "tag2", "tag3"],
        "content_body": "Full markdown content..."
    }}
    """
    
    user_prompt = f"""
    SOURCE MATERIAL:
    - Headline: {title}
    - Summary: {summary}
    - Link: {link}
    
    Write the article now. INCLUDE A TABLE.
    """
    
    for api_key in GROQ_API_KEYS:
        client = Groq(api_key=api_key)
        try:
            print(f"      🤖 AI Writing ({chosen_structure.split()[0]})...")
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.6,
                max_tokens=6500,
                response_format={"type": "json_object"}
            )
            return completion.choices[0].message.content
        except RateLimitError:
            print("      ⚠️ Rate Limit Hit, switching key...")
            time.sleep(2)
        except Exception: pass
    return None

# ==========================================
# 🏁 MAIN WORKFLOW
# ==========================================
def main():
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    print("🔥 ENGINE STARTED: VESLIFE PRO EDITION (IMAGE FIX + TABLES)")

    for source_name, rss_url in RSS_SOURCES.items():
        print(f"\n📡 Reading: {source_name}")
        feed = fetch_rss_feed(rss_url)
        if not feed: continue

        processed_count = 0
        
        for entry in feed.entries:
            if processed_count >= TARGET_PER_SOURCE:
                print(f"   🛑 Target reached for {source_name}")
                break
            
            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title, max_length=60, word_boundary=True)
            filename = f"{slug}.md"
            
            if os.path.exists(f"{CONTENT_DIR}/{filename}"): 
                continue
            
            print(f"   ⚡ Processing: {clean_title[:40]}...")
            
            author = random.choice(AUTHOR_PROFILES)
            raw_json = get_groq_article_json(clean_title, entry.summary, entry.link, author)
            
            if not raw_json: continue
            try:
                data = json.loads(raw_json)
            except:
                print("      ❌ JSON Parse Error")
                continue

            # 1. Generate Image (With Force-Filter)
            image_prompt = data.get('main_keyword', clean_title)
            final_img_path = generate_robust_image(image_prompt, f"{slug}.webp")
            
            # 2. Clean Content
            clean_body = clean_ai_content(data['content_body'])
            
            # 3. Inject Links
            final_body_with_links = inject_links_into_body(clean_body)
            
            # 4. Fallback Category
            if data.get('category') not in VALID_CATEGORIES:
                data['category'] = "Jeep News"

            # 5. Create Markdown File
            md_content = f"""---
title: "{data['title'].replace('"', "'")}"
date: {datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")}
author: "{author}"
categories: ["{data['category']}"]
tags: {json.dumps(data.get('tags', []))}
featured_image: "{final_img_path}"
description: "{data['description'].replace('"', "'")}"
slug: "{slug}"
url: "/{slug}/"
draft: false
weight: {random.randint(1, 10)}
---

{final_body_with_links}

---
*Reference: Analysis by {author} based on reports from [{source_name}]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f:
                f.write(md_content)
            
            # 6. Save & Index
            save_link_to_memory(data['title'], slug)
            
            full_url = f"{WEBSITE_URL}/articles/{slug}/"
            submit_to_indexnow(full_url)
            submit_to_google(full_url)

            print(f"      ✅ Published: {slug}")
            processed_count += 1
            
            print("      💤 Sleeping for 120s (Natural Drip Feed)...")
            time.sleep(120)

if __name__ == "__main__":
    main()
