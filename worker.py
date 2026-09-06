from dotenv import load_dotenv
load_dotenv()

from embeddings import embed_submission
import asyncio
from database import AsyncSessionLocal
from models import Submission
from llm_analysis import analyze_gap
from sqlalchemy import select

POLL_INTERVAL_SECONDS = 3

async def process_one_submission() -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Submission)
            .where(Submission.status == "pending")
            .order_by(Submission.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        submission = result.scalar_one_or_none()

        if submission is None:
            return False

        submission.status = "processing"
        await db.commit()

        try:
            new_embedding = embed_submission(
                submission.problem_title, submission.problem_statement, submission.wrong_code
            )

            similar_result = await db.execute(
                select(Submission)
                .where(
                    Submission.user_id == submission.user_id,
                    Submission.status == "complete",
                    Submission.id != submission.id,
                    Submission.embedding.isnot(None),
                )
                .order_by(Submission.embedding.cosine_distance(new_embedding))
                .limit(3)
            )
            similar_submissions = [
                {"problem_title": s.problem_title, "category": s.gap_category, "note": s.gap_note}
                for s in similar_result.scalars().all()
            ]
            print(f"Submission {submission.id}: found {len(similar_submissions)}similar past submissions: {[s['problem_title'] for s in similar_submissions]}")
            analysis = analyze_gap(
                submission.problem_title,
                submission.problem_statement,
                submission.wrong_code,
                submission.correct_code,
                similar_submissions=similar_submissions,
            )
            submission.gap_category = analysis["category"]
            submission.gap_note = analysis["note"]
            submission.topic_tags = ",".join(analysis.get("topic_tags", []))

            submission.embedding = new_embedding
            submission.status = "complete"
        except Exception as e:
            print(f"Submission {submission.id} failed: {e}")
            submission.status = "failed"

        await db.commit()
        return True

async def main():
    print("Worker started, polling for pending submissions...")
    while True:
        processed = await process_one_submission()
        if not processed:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(main())