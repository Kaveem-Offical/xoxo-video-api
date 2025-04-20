import json
from PIL import Image, ImageDraw, ImageFont
import os
import sys
import argparse
import numpy as np
import imageio.v3 as iio
from flask import Flask, request, send_file, jsonify
import threading
import time
import requests
from urllib.parse import urlparse

app = Flask(__name__)

# Configuration
CONFIG = {
    "canvas_size": (1080, 1920),
    "output_dir": "generated_images",
    "video_output_dir": "generated_videos",
    "font_path": "fonts/Poppins-Regular.ttf",
    "webhook_url": os.getenv("WEBHOOK_URL"),

    # Video settings
    "video_duration": 60,
    "video_fps": 30,

    # Font sizes
    "title_font_size": 50,
    "content_font_size_min": 20,
    "content_font_size_max": 40,
    "postid_font_size": 35,

    # Layout
    "image_top_margin": 30,
    "image_width": 1000,
    "image_height": 400,
    "text_spacing": {
        "image_title": 40,
        "title_content": 15,
        "content_postid": 15
    },
    "content_line_spacing_ratio": 0.2,
    "max_content_width": 980
}

def download_image(url, post_id):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        # Get file extension from URL
        parsed = urlparse(url)
        filename = f"{post_id}_image{os.path.splitext(parsed.path)[1]}"
        filepath = os.path.join(CONFIG['output_dir'], filename)

        with open(filepath, 'wb') as f:
            f.write(response.content)

        return filepath
    except Exception as e:
        raise ValueError(f"Failed to download image: {str(e)}")

def censor_text(text):
    return text

def load_posts(json_path):
    try:
        with open(json_path, 'r') as f:
            posts = json.load(f)

        required_fields = ['title', 'content', 'post_id', 'image']
        for i, post in enumerate(posts):
            for field in required_fields:
                if field not in post:
                    raise ValueError(f"Post {i+1} missing required field: {field}")

            post['title'] = censor_text(post['title'])
            post['content'] = censor_text(post['content'])

        return posts

    except FileNotFoundError:
        print(f"❌ Error: JSON file {json_path} not found!")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"❌ Error: Invalid JSON format in {json_path}")
        sys.exit(1)

def setup_fonts():
    try:
        title_font = ImageFont.truetype(CONFIG['font_path'], CONFIG['title_font_size'])
        postid_font = ImageFont.truetype(CONFIG['font_path'], CONFIG['postid_font_size'])
        return title_font, postid_font
    except IOError:
        print(f"❌ Error: Font file {CONFIG['font_path']} not found!")
        sys.exit(1)

def calculate_content_height(content, font_size, max_width):
    try:
        font = ImageFont.truetype(CONFIG['font_path'], font_size)
    except IOError:
        raise ValueError(f"Invalid font size {font_size}")

    lines = []
    words = content.split()
    current_line = []

    for word in words:
        test_line = ' '.join(current_line + [word])
        test_width = font.getlength(test_line)
        if test_width <= max_width:
            current_line.append(word)
        else:
            lines.append(' '.join(current_line))
            current_line = [word]
    if current_line:
        lines.append(' '.join(current_line))

    if not lines:
        return 0

    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    line_spacing = line_height * CONFIG['content_line_spacing_ratio']

    total_height = (len(lines) * line_height) + ((len(lines) - 1) * line_spacing)
    return total_height

def find_optimal_font_size(content, max_width, max_height):
    low = CONFIG['content_font_size_min']
    high = CONFIG['content_font_size_max']
    best_size = low

    for _ in range(10):
        if low > high:
            break

        mid = (low + high) // 2
        content_height = calculate_content_height(content, mid, max_width)

        if content_height <= max_height:
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1

    return best_size

def draw_wrapped_text(draw, text, position, font, max_width, line_spacing=5, fill="white"):
    lines = []
    words = text.split()
    line = ""

    for word in words:
        test_line = f"{line} {word}".strip()
        test_line_width = font.getlength(test_line)
        if test_line_width <= max_width:
            line = test_line
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)

    y = position[1]
    for line in lines:
        line_width = font.getlength(line)
        x = (CONFIG['canvas_size'][0] - line_width) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += font.getbbox(line)[3] + line_spacing
    return y

def generate_image(post, fonts):
    try:
        title_font, postid_font = fonts
        canvas = Image.new("RGB", CONFIG['canvas_size'], "black")
        draw = ImageDraw.Draw(canvas)

        # Download and validate image
        image_path = download_image(post["image"], post['post_id'])
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Downloaded image not found: {image_path}")

        try:
            img = Image.open(image_path).convert("RGB")
            img = img.resize((CONFIG['image_width'], CONFIG['image_height']))
            img_x = (CONFIG['canvas_size'][0] - CONFIG['image_width']) // 2
            canvas.paste(img, (img_x, CONFIG['image_top_margin']))
        finally:
            # Delete downloaded image immediately after use
            if os.path.exists(image_path):
                os.remove(image_path)
                print(f"🗑️ Deleted downloaded image: {image_path}")

        y_offset = CONFIG['image_top_margin'] + CONFIG['image_height'] + CONFIG['text_spacing']['image_title']
        y_offset = draw_wrapped_text(
            draw, post["title"], (0, y_offset),
            title_font, CONFIG['max_content_width'], line_spacing=10
        )

        y_offset += CONFIG['text_spacing']['title_content']
        post_id_text = f"Post ID: {post['post_id']}"
        post_id_bbox = postid_font.getbbox(post_id_text)
        post_id_height = post_id_bbox[3] - post_id_bbox[1]
        max_content_height = (CONFIG['canvas_size'][1] - y_offset) - (post_id_height + CONFIG['text_spacing']['content_postid'])

        optimal_content_size = find_optimal_font_size(
            post['content'],
            CONFIG['max_content_width'],
            max_content_height
        )

        content_font = ImageFont.truetype(CONFIG['font_path'], optimal_content_size)
        line_spacing = optimal_content_size * CONFIG['content_line_spacing_ratio']
        y_offset = draw_wrapped_text(
            draw, post["content"], (0, y_offset),
            content_font, CONFIG['max_content_width'], 
            line_spacing=line_spacing, fill="white"
        )

        y_offset += CONFIG['text_spacing']['content_postid']
        post_id_width = postid_font.getlength(post_id_text)
        post_id_x = (CONFIG['canvas_size'][0] - post_id_width) // 2
        draw.text((post_id_x, y_offset), post_id_text, font=postid_font, fill="white")

        return canvas

    except Exception as e:
        print(f"❌ Error generating image for post {post['post_id']}: {str(e)}")
        raise

def create_video_with_imageio(image_path, output_path, duration=60, fps=30):
    try:
        img = Image.open(image_path).convert("RGB")
        frame = np.array(img)
        frames = [frame] * (duration * fps)

        if output_path.endswith('.mp4'):
            output_path = output_path.replace('.mp4', '.avi')

        iio.imwrite(output_path, frames, fps=fps)
        print(f"✅ Created video: {os.path.basename(output_path)} ({duration}s)")
        return output_path
    except Exception as e:
        print(f"❌ Error creating video: {str(e)}")
        raise

def schedule_file_deletion(file_path, delay=600):
    def delete_file():
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🗑️ Deleted file: {file_path}")
        except Exception as e:
            print(f"❌ Error deleting file {file_path}: {str(e)}")

    timer = threading.Timer(delay, delete_file)
    timer.start()

@app.route('/generate-video', methods=['POST'])
def generate_video_api():
    try:
        if 'json_file' not in request.files:
            return jsonify({"error": "No JSON file provided"}), 400

        json_file = request.files['json_file']
        os.makedirs(CONFIG['output_dir'], exist_ok=True)
        os.makedirs(CONFIG['video_output_dir'], exist_ok=True)

        json_path = os.path.join(CONFIG['output_dir'], 'temp_data.json')
        json_file.save(json_path)

        posts = load_posts(json_path)
        fonts = setup_fonts()

        if not posts:
            return jsonify({"error": "No valid posts found in JSON"}), 400

        post = posts[0]
        print(f"Processing post {post['post_id']}")

        image = generate_image(post, fonts)
        image_path = os.path.join(CONFIG['output_dir'], f"{post['post_id']}.png")
        image.save(image_path)

        video_path = os.path.join(CONFIG['video_output_dir'], f"{post['post_id']}.avi")
        create_video_with_imageio(image_path, video_path, CONFIG['video_duration'], CONFIG['video_fps'])

        # Cleanup generated files
        if os.path.exists(image_path):
            os.remove(image_path)
            print(f"🗑️ Deleted generated image: {image_path}")

        schedule_file_deletion(video_path)

        return send_file(
            video_path,
            mimetype='video/x-msvideo',
            as_attachment=True,
            download_name=f"{post['post_id']}.avi"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def main():
    parser = argparse.ArgumentParser(description='Generate videos from JSON posts')
    parser.add_argument('--json', type=str, required=True, help='Path to JSON data file')
    parser.add_argument('--image-output', type=str, default=CONFIG['output_dir'], help='Output directory for images')
    parser.add_argument('--video-output', type=str, default=CONFIG['video_output_dir'], help='Output directory for videos')
    parser.add_argument('--duration', type=int, default=CONFIG['video_duration'], help='Video duration in seconds')
    args = parser.parse_args()

    CONFIG['output_dir'] = args.image_output
    CONFIG['video_output_dir'] = args.video_output
    CONFIG['video_duration'] = args.duration

    os.makedirs(CONFIG['output_dir'], exist_ok=True)
    os.makedirs(CONFIG['video_output_dir'], exist_ok=True)

    print(f"🚀 Processing JSON file: {args.json}")
    posts = load_posts(args.json)
    fonts = setup_fonts()

    success_count = 0
    for post in posts:
        try:
            print(f"\n🔄 Processing post {post['post_id']}")
            image = generate_image(post, fonts)
            image_path = os.path.join(CONFIG['output_dir'], f"{post['post_id']}.png")
            image.save(image_path)

            video_path = os.path.join(CONFIG['video_output_dir'], f"{post['post_id']}.avi")
            create_video_with_imageio(image_path, video_path, CONFIG['video_duration'], CONFIG['video_fps'])

            # Cleanup
            if os.path.exists(image_path):
                os.remove(image_path)
                print(f"🗑️ Deleted generated image: {image_path}")
            schedule_file_deletion(video_path)

            success_count += 1
        except Exception as e:
            print(f"❌ Failed to process {post['post_id']}: {str(e)}")

    print(f"\n🎉 Processed {success_count}/{len(posts)} posts successfully")
    print(f"📁 Temporary videos available in: {os.path.abspath(CONFIG['video_output_dir'])}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        print("🚀 Starting API server on port 5000...")
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
