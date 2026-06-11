import os
import requests
import json
import base64
import sys
import cv2
import numpy as np

# Initialize Haar Cascades
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
body_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_fullbody.xml')
cat_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalcatface.xml')

def fast_pass(image_path):
    """
    Returns True if OpenCV detects a face, body, or cat face.
    This is used to filter images before sending them to expensive LLM inference.
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            return False
            
        # Downscale for speed
        h, w = img.shape[:2]
        scale = 400.0 / w
        small = cv2.resize(img, (400, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        # Detect faces
        faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(30, 30))
        if len(faces) > 0:
            return True
            
        # Detect bodies
        bodies = body_cascade.detectMultiScale(gray, 1.1, 3, minSize=(50, 100))
        if len(bodies) > 0:
            return True
            
        # Detect cats
        cats = cat_cascade.detectMultiScale(gray, 1.1, 3, minSize=(30, 30))
        if len(cats) > 0:
            return True
            
        return False
    except Exception as e:
        print(f"Fast pass error for {image_path}: {e}")
        return False

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_image(image_path, api_key, model="google/gemma-4-31b-it"):
    if not os.path.exists(image_path):
        return None

    base64_image = encode_image(image_path)
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze this webcam image. Detect if there are 'person', 'dog', or 'cat' present. Return ONLY a valid JSON object like this: {\"person\": true, \"dog\": false, \"cat\": false}. Do not include any other text."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
    }
    
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            data=json.dumps(payload)
        )
        if response.status_code != 200:
            print(f"Error {response.status_code}: {response.text}")
            return None
        result = response.json()
        if 'choices' not in result or not result['choices']:
            return None
            
        content = result['choices'][0]['message']['content'].strip()
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            return json.loads(content)
    except Exception as e:
        print(f"Error analyzing {image_path}: {e}")
        return None

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY environment variable not set.")
        sys.exit(1)
        
    watch_dirs = ["/mnt/models/Webcam21", "/mnt/models/Webcam22"]
    base_dir = "/home/user/webcam"
    analysis_file = os.path.join(base_dir, "analysis.json")
    
    if os.path.exists(analysis_file):
        with open(analysis_file, 'r') as f:
            analysis_data = json.load(f)
    else:
        analysis_data = {}

    for image_dir in watch_dirs:
        if not os.path.exists(image_dir):
            continue
            
        print(f"Scanning {image_dir}...")
        images = [f for f in os.listdir(image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
        
        # Sort by date (newest first) if possible
        images.sort(key=lambda x: os.path.getmtime(os.path.join(image_dir, x)), reverse=True)

        new_analysis = False
        processed_count = 0
        
        for img in images:
            if img in analysis_data:
                continue
                
            image_path = os.path.join(image_dir, img)
            
            # FAST PASS: OpenCV Haar Cascades
            if fast_pass(image_path):
                print(f"Candidate detected in {img}. Triggering deep pass...")
                result = analyze_image(image_path, api_key)
                if result:
                    analysis_data[img] = result
                    new_analysis = True
                    processed_count += 1
            else:
                # No candidate, save a negative result to avoid reprocessing
                analysis_data[img] = {"person": False, "dog": False, "cat": False, "fast_pass": "negative"}
                new_analysis = True
                # We don't count these towards processed_count limit as they are cheap
            
            if processed_count >= 20: # Limit deep passes per run
                break
        
        if new_analysis:
            with open(analysis_file, 'w') as f:
                json.dump(analysis_data, f, indent=2)
            print(f"Updated {analysis_file}")

if __name__ == "__main__":
    main()
