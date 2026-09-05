import os
import cohere

co = cohere.ClientV2(api_key=os.getenv("COHERE_API_KEY"))

def embed_submission(problem_title: str, problem_statement: str, wrong_code: str) -> list[float]:
    text = f"Problem: {problem_title}\n{problem_statement}\n\nCode:\n{wrong_code}"
    response = co.embed(
        texts=[text],
        model="embed-v4.0",
        input_type="search_document",
        output_dimension=1024,
        embedding_types=["float"],
    )
    return response.embeddings.float_[0]