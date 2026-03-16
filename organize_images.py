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
    organize_images()    <head>
        <meta charset="UTF-8">
        <title>Nari Nakhre | Premium Ethnic Jewelry & Accessories Wholesaler</title>
        <link rel="icon" type="image/png" href="{{ url_for('static', filename='assets/logo.png') }}">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta name="description" content="Wholesale Indian jewelry, bridal sets, and traditional bangles. Shop premium ethnic accessories for weddings, festivals, and special occasions.">
        <!-- Open Graph / Facebook / WhatsApp -->
        <meta property="og:title" content="Nari Nakhre | Premium Ethnic Jewelry & Accessories Wholesaler">
        <meta property="og:description" content="Wholesale Indian jewelry, bridal sets, and traditional bangles. Shop premium ethnic accessories for weddings, festivals, and special occasions.">
        <meta property="og:image" content="https://raga2026.pythonanywhere.com/static/assets/logo.png">
        <meta property="og:url" content="https://raga2026.pythonanywhere.com">
        <meta property="og:type" content="website">
        <!-- Twitter Card -->
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:title" content="Nari Nakhre | Premium Ethnic Jewelry & Accessories Wholesaler">
        <meta name="twitter:description" content="Wholesale Indian jewelry, bridal sets, and traditional bangles. Shop premium ethnic accessories for weddings, festivals, and special occasions.">
        <meta name="twitter:image" content="https://raga2026.pythonanywhere.com/static/assets/logo.png">
    </head>