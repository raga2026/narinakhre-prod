import os
import sqlite3
import shutil

# This ensures we are always in the NariNakhre folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(BASE_DIR, 'uploads_q')  # Changed to match your folder name
TARGET_DIR = os.path.join(BASE_DIR, 'static', 'assets', 'products')

def organize_images():
    print(f"DEBUG: Looking in {SOURCE_DIR}")
    print(f"DEBUG: Moving to {TARGET_DIR}")

    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)

    if not os.path.exists(SOURCE_DIR):
        print(f"CRITICAL ERROR: Folder {SOURCE_DIR} does not exist!")
        return

    found_skus = 0
    for sku in os.listdir(SOURCE_DIR):
        sku_path = os.path.join(SOURCE_DIR, sku)
        if os.path.isdir(sku_path):
            images = [f for f in os.listdir(sku_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if images:
                found_skus += 1
                for idx, img in enumerate(sorted(images)[:5], 1):
                    new_name = f"{sku}_{idx}.jpg"
                    shutil.copy(os.path.join(sku_path, img), os.path.join(TARGET_DIR, new_name))
                    print(f"Success: Moved {new_name}")

    if found_skus == 0:
        print("WARNING: No SKU folders with images were found inside uploads_q.")
    else:
        print(f"Done! Successfully processed {found_skus} SKUs.")

if __name__ == "__main__":
    organize_images()