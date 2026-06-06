from fastapi import FastAPI
from pydantic import BaseModel
from main import ask_igla

app = FastAPI()


class SituationRequest(BaseModel):
    situation: str


@app.post("/ask")
def ask_endpoint(request: SituationRequest):
    response = ask_igla(request.situation)
    return {"response": response}