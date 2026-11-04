
import fitz
import os

COURSE_FOLDER = "Courses"

for filename in os.listdir(COURSE_FOLDER):

    if filename.lower().endswith(".pdf"):

        file_path = os.path.join(COURSE_FOLDER, filename)

        doc = fitz.open(file_path)

        print("\n" + "=" * 60)
        print("FILE:", filename)
        print("TOTAL PAGES:", len(doc))
        print("=" * 60)

        for page_number, page in enumerate(doc):

            text = page.get_text()

            print(f"\n--- Page {page_number + 1} ---")
            print(text[:1000])

        doc.close()