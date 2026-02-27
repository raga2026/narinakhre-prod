import os
from PIL import Image

def create_thumbnail(image_path, thumb_path, max_width=300):
    try:
        with Image.open(image_path) as img:
            w, h = img.size
            if w > max_width:
                new_h = int(h * max_width / w)
                img = img.resize((max_width, new_h), Image.LANCZOS)
            img.save(thumb_path, quality=90)
    except Exception as e:
        print(f"Thumbnail error for {image_path}: {e}")

def main():
    products_dir = os.path.join('static', 'assets', 'products')
    thumbs_dir = os.path.join(products_dir, 'thumbs')
    os.makedirs(thumbs_dir, exist_ok=True)
    for fname in os.listdir(products_dir):
        fpath = os.path.join(products_dir, fname)
        if os.path.isfile(fpath) and fname.lower().endswith(('.jpg', '.jpeg', '.png')):
            thumb_path = os.path.join(thumbs_dir, fname)
            if not os.path.exists(thumb_path):
                print(f"Creating thumbnail for {fname}")
                create_thumbnail(fpath, thumb_path)

if __name__ == '__main__':
    main()
