from fastapi import FastAPI, Request
from canvas_parser import load_all_texts_from_folder
from json_parser import load_json_from_folder
from llm_engine import ask_llm
from ppt_parser import load_all_ppts


from pydantic import BaseModel

class ChatRequest(BaseModel):
    question: str

app = FastAPI()



@app.on_event("startup")
def load_context():
    print("🔄 Loading context from data and json folders...")

    pdf_chunks = load_all_texts_from_folder("data")
    json_chunks = load_json_from_folder("json")
    ppt_chunks = load_all_ppts("ppt")

    # Load only a few entries to minimize token use
    pdf_text = "\n\n".join(c["content"] for c in pdf_chunks[:3])  # up to 3 PDFs
    json_text = "\n".join(json_chunks[:10])  # up to 10 JSON entries
    ppt_text = "\n".join(c["content"] for c in ppt_chunks[:2])  # cap to 2 PPTs

    global all_context
    all_context = (pdf_text + "\n" + json_text + "\n" + ppt_text).strip()
    print(f"✅ Final combined context loaded ({len(all_context)} characters)")
    print(all_context[:500])  # Debug: show the beginning of the context
    print("---------------------------------------------------")

@app.post("/chat")
async def chat(payload: ChatRequest):
    question = payload.question

    if not question:
        return {"error": "No question provided"}

    # Use combined global context from both PDF and JSON
    answer = ask_llm(question, all_context[:60000])  # limit characters for token safety
    return {"answer": answer}
