
import fitz
import os

# Project directories
COURSE_FOLDER = "Courses"
OUTPUT_FOLDER = os.path.join("data", "extracted_text")

# Create output directory if it doesn't exist
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def extract_pdf_text():

    # Get all PDF files from Courses folder
    pdf_files = [
        file for file in os.listdir(COURSE_FOLDER)
        if file.lower().endswith(".pdf")
    ]

    if not pdf_files:
        print("No PDF files found in Courses folder.")
        return

    print(f"Found {len(pdf_files)} PDF files.\n")

    for filename in pdf_files:

        pdf_path = os.path.join(COURSE_FOLDER, filename)

        # Generate corresponding TXT filename
        txt_filename = os.path.splitext(filename)[0] + ".txt"
        txt_path = os.path.join(OUTPUT_FOLDER, txt_filename)

        print(f"Processing: {filename}")

        try:
            doc = fitz.open(pdf_path)

            total_pages = len(doc)
            total_characters = 0
            empty_pages = []

            with open(txt_path, "w", encoding="utf-8") as txt_file:

                txt_file.write(f"DOCUMENT: {filename}\n")
                txt_file.write(f"TOTAL PAGES: {total_pages}\n")
                txt_file.write("=" * 70 + "\n\n")

                # Extract text page by page
                for page_number, page in enumerate(doc, start=1):

                    page_text = page.get_text("text")

                    if not page_text.strip():
                        empty_pages.append(page_number)

                    total_characters += len(page_text)

                    txt_file.write(
                        f"\n--- PAGE {page_number} ---\n\n"
                    )

                    txt_file.write(page_text)
                    txt_file.write("\n")

            doc.close()

            print(f"  Pages: {total_pages}")
            print(f"  Characters: {total_characters}")
            print(f"  Saved: {txt_path}")

            if empty_pages:
                print(f"  Warning: Empty pages: {empty_pages}")

            print("-" * 50)

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    print("\nPDF EXTRACTION COMPLETED!")


if __name__ == "__main__":
    extract_pdf_text()
