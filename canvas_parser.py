import pdfplumber
from pathlib import Path

def extract_text_from_pdf(file_path):
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text.strip()

def load_all_texts_from_folder(folder="data"):
    chunks = []
    for file in Path(folder).glob("*.pdf"):
        content = extract_text_from_pdf(file)
        if content:
            chunks.append({"filename": file.name, "content": content})
    return chunks
