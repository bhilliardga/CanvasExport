import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ask_llm(question, context):
    prompt = f"Here is the course material:\n{context}\n\nNow answer this question:\n{question}"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful tutor who only answers based on the course materials provided."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content
