import os
import requests
import json
import base64
import sys
import cv2
import numpy as np
import time
from datetime import datetime

# CONFIGURATION
MODEL_CLOUD = "google/gemma-4-31b-it"
MODEL_LOCAL = "gemma-4:12b" # User belief: we can run this slowly
BURST_THRESHOLD_SECONDS = 120 # Group images within 2 mins
MIN_MEM_FOR_LOCAL_GB = 16.0

# Initialize Haar Cascades
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
body_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_fullbody.xml')
cat_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalcatface.xml')

def fast_pass(image_path):
    try:
        img = cv2.imread(image_path)
        if img is None: return {}
        h, w = img.shape[:2]
        scale = 400.0 / w
        small = cv2.resize(img, (400, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        results = {}
        # Increased sensitivity: lower minNeighbors, smaller minSize
        faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20, 20))
        if len(faces) > 0: results["face"] = True
            
        bodies = body_cascade.detectMultiScale(gray, 1.1, 2, minSize=(40, 80))
        if len(bodies) > 0: results["body"] = True
            
        cats = cat_cascade.detectMultiScale(gray, 1.1, 2, minSize=(20, 20))
        if len(cats) > 0: results["cat"] = True
            
        return results
    except Exception as e:
        print(f"Fast pass error: {e}")
        return {}

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_image_openrouter(image_path, api_key):
    base64_image = encode_image(image_path)
    headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }
    
    payload = {
        "model": MODEL_CLOUD,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze this webcam image. Specifically detect if any PERSON, FACE, BODY, DOG, or CAT is visible. If you see a human (even partial), use keys 'person', 'face', or 'body'. If you see something unusual (e.g. alien_ufo), add a descriptive key for it. Return ONLY a valid JSON object with boolean keys. Example: {\"person\": true, \"face\": true, \"dog\": false}"
                    },
                    {
                        "type": "image_url",
                        "image_url": { "url": f"data:image/jpeg;base64,{base64_image}" }
                    }
                ]
            }
        ],
        "response_format": { "type": "json_object" }
    }
    
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=60)
        if response.status_code != 200: return None
        content = response.json()['choices'][0]['message']['content'].strip()
        return json.loads(content)
    except:
        return None

def analyze_image_local(image_path):
    """Placeholder for Ollama local inference if memory allows."""
    # Since we can't confirm Ollama's exact API without testing, 
    # we'll assume a standard Ollama-like local endpoint or a mock for now.
    # In a real setup, this would call `http://localhost:11434/api/generate`
    print(f"DEBUG: Attempting local inference with {MODEL_LOCAL} (Memory > 16GB)")
    # For now, we return None to fall back to OpenRouter unless user specifically sets up Ollama
    return None

def analyze_burst_openrouter(image_paths, api_key):
    """Analyze a sequence of images for a textual description."""
    content_list = [{ "type": "text", "text": "These images were taken in a sequence. Describe what is happening across this time period. Who is there? What are they doing?" }]
    
    for path in image_paths:
        base64_img = encode_image(path)
        content_list.append({
            "type": "image_url",
            "image_url": { "url": f"data:image/jpeg;base64,{base64_img}" }
        })

    payload = {
        "model": MODEL_CLOUD,
        "messages": [{ "role": "user", "content": content_list }]
    }
    
    try:
        headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=90)
        return response.json()['choices'][0]['message']['content'].strip()
    except:
        return "Failed to analyze sequence."

def get_free_mem_gb():
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
            free = [l for l in lines if l.startswith('MemAvailable')][0]
            return int(free.split()[1]) / (1024 * 1024)
    except:
        return 0

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    watch_dirs = ["/mnt/models/Webcam21", "/mnt/models/Webcam22"]
    base_dir = "/home/user/webcam"
    analysis_file = os.path.join(base_dir, "analysis.json")
    burst_file = os.path.join(base_dir, "bursts.json")
    
    analysis_data = json.load(open(analysis_file)) if os.path.exists(analysis_file) else {}
    burst_data = json.load(open(burst_file)) if os.path.exists(burst_file) else {}

    free_mem = get_free_mem_gb()
    can_run_local = free_mem >= MIN_MEM_FOR_LOCAL_GB
    print(f"System Check: Free Memory = {free_mem:.1f}GB. Local LLM Enabled: {can_run_local}")

    for image_dir in watch_dirs:
        if not os.path.exists(image_dir): continue
        print(f"Scanning {image_dir}...")
        
        images = [f for f in os.listdir(image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
        
        # Catch-up prioritization:
        # 1. Images not in analysis_data at all
        # 2. Images marked as "partial" (OpenCV hit, but no LLM yet)
        missing = [i for i in images if i not in analysis_data]
        partials = [i for i in images if i in analysis_data and analysis_data[i].get("fast_pass") == "partial"]
        
        # Combine: newest missing first, then partials
        queue = sorted(missing, key=lambda x: os.path.getmtime(os.path.join(image_dir, x)), reverse=True) + partials
        
        if not queue:
            print(f"No pending analysis for {image_dir}")
            continue

        new_analysis = False
        deep_pass_count = 0
        max_deep_passes = 15 # Batch size
        
        for img in queue:
            image_path = os.path.join(image_dir, img)
            
            # If it's a partial, we already have fp_results
            if img in analysis_data and analysis_data[img].get("fast_pass") == "partial":
                fp_results = {k:v for k,v in analysis_data[img].items() if k != "fast_pass"}
            else:
                fp_results = fast_pass(image_path)
            
            if fp_results:
                # If we haven't done deep analysis yet
                if img not in analysis_data or analysis_data[img].get("fast_pass") == "partial":
                    print(f"Deep Pass Required for {img}: {fp_results}")
                    result = None
                    if can_run_local:
                        result = analyze_image_local(image_path)
                    
                    if not result and api_key and deep_pass_count < max_deep_passes:
                        result = analyze_image_openrouter(image_path, api_key)
                        deep_pass_count += 1
                    
                    if result:
                        analysis_data[img] = result
                        new_analysis = True
                    else:
                        # Still partial (limit reached or failed)
                        analysis_data[img] = {**fp_results, "fast_pass": "partial"}
                        new_analysis = True
            else:
                # Negative fast pass
                analysis_data[img] = { "fast_pass": "negative" }
                new_analysis = True

            if deep_pass_count >= max_deep_passes:
                print(f"Batch limit ({max_deep_passes}) reached for {image_dir}")
                break

        # 2. Image Bursts Detection
        bursts = []
        if images:
            current_burst = [images[0]]
            for i in range(1, len(images)):
                t1 = os.path.getmtime(os.path.join(image_dir, images[i-1]))
                t2 = os.path.getmtime(os.path.join(image_dir, images[i]))
                if t2 - t1 < BURST_THRESHOLD_SECONDS:
                    current_burst.append(images[i])
                else:
                    if len(current_burst) >= 2: bursts.append(current_burst)
                    current_burst = [images[i]]
            if len(current_burst) >= 2: bursts.append(current_burst)

        # 3. Burst Analysis
        for burst in bursts:
            burst_id = burst[-1] # Use last image as ID
            if burst_id in burst_data: continue
            
            # Only analyze burst if at least one image has a detection
            has_detection = any(img in analysis_data and any(v is True for k, v in analysis_data[img].items() if k != 'fast_pass') for img in burst)
            
            if has_detection and api_key:
                print(f"Analyzing burst ending at {burst_id}...")
                full_paths = [os.path.join(image_dir, f) for f in burst[-3:]] # Take last 3 max
                summary = analyze_burst_openrouter(full_paths, api_key)
                burst_data[burst_id] = { "summary": summary, "images": burst }
                new_analysis = True

        if new_analysis:
            with open(analysis_file, 'w') as f: json.dump(analysis_data, f, indent=2)
            with open(burst_file, 'w') as f: json.dump(burst_data, f, indent=2)
            print(f"Updated data files.")

if __name__ == "__main__":
    main()
