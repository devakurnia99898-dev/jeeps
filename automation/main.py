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
# ⚙️ CONFIGURATION & SETUP (VESLIFE / JEEP MECHANIC)
# ==========================================

GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "") 
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

WEBSITE_URL = "https://veslife.biz.id" 
INDEXNOW_KEY = "e74819b68a0f40e98f6ec3dc24f610f0" 
GOOGLE_JSON_KEY = os.environ.get("GOOGLE_INDEXING_KEY", "") 

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    exit(1)

# 🔥 AUTHOR BARU: Persona Mekanik & Ahli DIY
AUTHOR_PROFILES = [
    "Dave Harsya (Certified 4x4 Mechanic)", 
    "Sarah Jenkins (Overland Build Expert)",
    "Luca Romano (Restoration Specialist)", 
    "Marcus Reynolds (Suspension Engineer)",
    "Ben Foster (Trail Recovery Instructor)"
]

# Kategori Spesifik (Teknis/Guide)
VALID_CATEGORIES = [
    "Wrangler & Gladiator", "Grand Cherokee", "Maintenance Guides", 
    "Off-Road Mods", "Troubleshooting", "EV & Hybrid 4xe"
]

# Sumber RSS (Updated & Stabil)
RSS_SOURCES = {
    "Autoblog Jeep": "https://www.autoblog.com/category/jeep/rss.xml",
    "Motor1 News": "https://www.motor1.com/rss/news/all/",
    "Mopar Insiders": "https://moparinsiders.com/feed/", 
    "Jeep News": "https://www.autoevolution.com/rss/cars/jeep/",
    "Jalopnik": "https://jalopnik.com/rss"
}

CONTENT_DIR = "content/articles" 
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"

# Target per sumber (Total bisa 8-10 artikel per run)
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
    if len(memory) > 500: memory = dict(list(memory.items())[-500:])
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def fetch_rss_feed(url):
    """
    Mengambil RSS dengan Header Browser Lengkap (Anti-Block)
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.google.com/',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    try:
        print(f"      ... Menghubungi Server...")
        response = requests.get(url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            feed = feedparser.parse(response.content)
            if len(feed.entries) > 0:
                print(f"      ✅ Berhasil! Ditemukan {len(feed.entries)} artikel.")
                return feed
            else:
                print(f"      ⚠️ Status 200 OK, tapi RSS Kosong/Format Salah.")
                return None
        else:
            print(f"      ❌ Gagal: HTTP Status {response.status_code}")
            return None
            
    except Exception as e:
        print(f"      ❌ Error Koneksi: {e}")
        return None

def clean_ai_content(text):
    if not text: return ""
    text = re.sub(r'^```[a-zA-Z]*\n', '', text)
    text = re.sub(r'\n```$', '', text)
    text = text.replace("```", "")
    
    # Hapus Intro/Outro Basi
    text = re.sub(r'^##\s*(Introduction|Conclusion|Summary|The Verdict|Final Thoughts|In Conclusion)\s*\n', '', text, flags=re.MULTILINE|re.IGNORECASE)
    
    # Formatting Markdown
    text = text.replace("<h1>", "# ").replace("</h1>", "\n")
    text = text.replace("<h2>", "## ").replace("</h2>", "\n")
    text = text.replace("<h3>", "### ").replace("</h3>", "\n")
    text = text.replace("<h4>", "#### ").replace("</h4>", "\n")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<p>", "").replace("</p>", "\n\n")
    return text.strip()

# ==========================================
# 📑 AUTO TOC (NAVIGASI)
# ==========================================
def generate_toc(content_body):
    toc_lines = ["**Table of Contents**\n"]
    headers = re.findall(r'^(#{2,3})\s+(.+)$', content_body, flags=re.MULTILINE)
    
    if not headers: return ""

    for level, title in headers:
        anchor = slugify(title)
        if level == "##":
            toc_lines.append(f"- [{title}](#{anchor})")
        elif level == "###":
            toc_lines.append(f"  - [{title}](#{anchor})")
    
    return "\n".join(toc_lines) + "\n\n---\n\n"

# ==========================================
# 🧠 SMART SILO LINKING
# ==========================================
def get_contextual_links(current_title):
    memory = load_link_memory()
    items = list(memory.items())
    if not items: return []
    
    stop_words = ['the', 'a', 'an', 'in', 'on', 'at', 'for', 'to', 'of', 'and', 'with', 'is', 'jeep', 'how', 'to'] 
    keywords = [w.lower() for w in current_title.split() if w.lower() not in stop_words and len(w) > 3]
    
    relevant_links = []
    for title, url in items:
        title_lower = title.lower()
        match_score = sum(1 for k in keywords if k in title_lower)
        if match_score > 0:
            relevant_links.append((title, url))
    
    if relevant_links:
        count = min(3, len(relevant_links))
        return random.sample(relevant_links, count)
    
    count = min(3, len(items))
    return random.sample(items, count)

def inject_links_into_body(content_body, current_title):
    links = get_contextual_links(current_title)
    if not links: return content_body

    link_box = "\n\n> **🔧 Related Guides:**\n"
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
# 🎨 IMAGE GENERATOR (WORKSHOP STYLE)
# ==========================================
def generate_robust_image(prompt, filename):
    output_path = f"{IMAGE_DIR}/{filename}"
    forbidden_words = ["sedan", "coupe", "bmw", "mercedes", "toyota", "sports car", "track"]
    clean_prompt = prompt.lower().replace('"', '').replace("'", "")
    for word in forbidden_words:
        clean_prompt = clean_prompt.replace(word, "")
    
    # 🔥 GAYA VISUAL: Mekanik, Bengkel, Detail Parts, Lumpur
    forced_style = "Jeep Wrangler offroad, mechanic workshop setting, detailed car parts, engine bay close up, realistic automotive photography, 8k resolution, cinematic lighting"
    
    final_prompt = f"{clean_prompt}, {forced_style}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://google.com"
    }

    print(f"      🎨 Generating Image: {clean_prompt[:30]}...")

    # 1. POLLINATIONS (Priority)
    try:
        seed = random.randint(1, 99999)
        poly_url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(final_prompt)}?width=1280&height=720&model=flux&seed={seed}&nologo=true"
        resp = requests.get(poly_url, headers=headers, timeout=25)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(output_path, "WEBP", quality=90)
            print("      ✅ Image Saved (Pollinations)")
            return f"/images/{filename}"
    except Exception: pass

    # 2. HERCAI (Fallback)
    try:
        hercai_url = f"https://hercai.onrender.com/v3/text2image?prompt={requests.utils.quote(final_prompt)}"
        resp = requests.get(hercai_url, headers=headers, timeout=40)
        if resp.status_code == 200:
            data = resp.json()
            if "url" in data:
                img_data = requests.get(data["url"], headers=headers, timeout=20).content
                img = Image.open(BytesIO(img_data)).convert("RGB")
                img.save(output_path, "WEBP", quality=90)
                print("      ✅ Image Saved (Hercai)")
                return f"/images/{filename}"
    except Exception: pass

    # 3. FLICKR (Final Safety)
    try:
        flickr_url = f"https://loremflickr.com/1280/720/jeep,mechanic,garage/all"
        resp = requests.get(flickr_url, headers=headers, timeout=20, allow_redirects=True)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(output_path, "WEBP", quality=90)
            print("      ✅ Image Saved (Real Photo)")
            return f"/images/{filename}"
    except Exception: pass

    return "/images/default-jeep.webp"

# ==========================================
# 🧠 CONTENT ENGINE (THE DEEP DIVE PROMPT 1500+ WORDS)
# ==========================================

def get_groq_article_json(title, summary, link, author_name):
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # 🔥 SYSTEM PROMPT YANG "MEMAKSA" PANJANG & LENGKAP
    system_prompt = f"""
    You are {author_name}, a Master Jeep Mechanic and Off-road Engineer with 20 years of experience.
    Current Date: {current_date}.
    
    OBJECTIVE: Write a COMPREHENSIVE, LONG-FORM guide (Target: 1500+ words).
    INPUT CONTEXT: You will receive a headline. You must PIVOT this into a Deep Dive Technical Guide.
    
    RULE: Do NOT write a short news summary. Use the headline as a seed, then EXPAND using your expert knowledge about the specific Jeep models mentioned.
    
    ✅ MANDATORY ARTICLE STRUCTURE (DO NOT SKIP ANY SECTION):
    
    1. **The Diagnostic / The Situation** (H2):
       - Explain the technical background. Why is this important?
       - Symptoms or Signs to look for.
       
    2. **Technical Deep Dive** (H2):
       - How the system works (Engine, Suspension, or Electrical).
       - Common failure points (be specific, e.g., "The plastic impeller in the water pump").
       
    3. **Pre-Work Checklist** (H2):
       - **At a Glance Table** (Markdown): Difficulty (1-5), Time, Cost, DIY vs Shop.
       - **Tools Required** (H3): Detailed bullet points (e.g., "10mm Socket", "Torque Wrench").
       - **Safety Warnings** (H3): Critical safety info.
       
    4. **Step-by-Step Guide** (H2):
       - THIS MUST BE THE LONGEST SECTION.
       - Use H3 (###) for each major step.
       - Use H4 (####) for detailed sub-steps.
       - Include specific torque specs (Nm/ft-lbs) where relevant.
       
    5. **Pro Tips & Common Mistakes** (H2):
       - "Don't do this" advice.
       - Tricks of the trade.
       
    6. **Cost Analysis: DIY vs Mechanic** (H2):
       - Breakdown of parts cost vs labor cost.
       
    7. **Frequently Asked Questions** (H2):
       - 5 Detailed FAQs relevant to the topic.
    
    OUTPUT FORMAT (JSON):
    {{
        "title": "A detailed 'How-to' or 'Ultimate Guide' title",
        "description": "Meta description (160 chars) focusing on solution",
        "category": "One of: {', '.join(VALID_CATEGORIES)}",
        "main_keyword": "Mechanic working on Jeep [specific part]...",
        "tags": ["diy", "maintenance", "guide", "jeep tech"],
        "content_body": "Full markdown content with all sections above..."
    }}
    """
    
    user_prompt = f"""
    SOURCE NEWS HEADLINE: "{title}"
    SOURCE SUMMARY: "{summary}"
    
    TASK: Ignore the length of the source. Use your EXPERT KNOWLEDGE to write a 1500-word Masterclass Guide based on this topic.
    If the news is about a new model, write a "Technical Prep Guide" for it.
    If the news is generic, write a "Maintenance Masterclass" related to the keyword.
    
    GO DEEP. DON'T BE LAZY.
    """
    
    for api_key in GROQ_API_KEYS:
        client = Groq(api_key=api_key)
        try:
            print(f"      🤖 AI Writing (DEEP DIVE MODE - 1500+ Words)...")
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.6, # Sedikit kreatif agar bisa expand panjang
                max_tokens=7000, # Max token ditingkatkan
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

    print("🔥 ENGINE STARTED: VESLIFE 'MECHANIC PRO' (LONG FORM 1500+ WORDS)")

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
            # Slug dari judul asli dulu
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

            # Update slug jika judul berubah drastis (agar URL relevan dengan konten How-To)
            new_slug = slugify(data['title'], max_length=60, word_boundary=True)
            if new_slug != slug:
                filename = f"{new_slug}.md"
                slug = new_slug

            # 1. Generate Image (Workshop Style)
            image_prompt = data.get('main_keyword', clean_title)
            final_img_path = generate_robust_image(image_prompt, f"{slug}.webp")
            
            # 2. Clean Content
            clean_body = clean_ai_content(data['content_body'])
            
            # 3. Generate TOC + Links
            toc_content = generate_toc(clean_body)
            body_with_links = inject_links_into_body(clean_body, data['title'])
            
            # Gabungkan: TOC + Body
            final_body = toc_content + body_with_links
            
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

{final_body}

---
*Reference: Technical analysis by {author} based on reports from [{source_name}]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f:
                f.write(md_content)
            
            save_link_to_memory(data['title'], slug)
            
            full_url = f"{WEBSITE_URL}/articles/{slug}/"
            submit_to_indexnow(full_url)
            submit_to_google(full_url)

            print(f"      ✅ Published: {slug}")
            processed_count += 1
            
            print("      💤 Sleeping for 120s...")
            time.sleep(60)

if __name__ == "__main__":
    main()
