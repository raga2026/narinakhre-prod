import os

def rename_images_in_folder(base_folder):
    for folder_name in os.listdir(base_folder):
        folder_path = os.path.join(base_folder, folder_name)
        if os.path.isdir(folder_path):
            images = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
            images.sort()
            for idx, image in enumerate(images, 1):
                ext = os.path.splitext(image)[1]
                new_name = f"{folder_name}-{idx}{ext}"
                src = os.path.join(folder_path, image)
                dst = os.path.join(folder_path, new_name)
                os.rename(src, dst)
            print(f"Renamed {len(images)} images in {folder_name}")

if __name__ == "__main__":
    base_folder = input("Enter the path to the base folder: ")
    rename_images_in_folder(base_folder)
    print("Done.")
