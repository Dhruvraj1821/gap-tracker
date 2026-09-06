from dotenv import load_dotenv
load_dotenv()

from llm_analysis import analyze_gap

result = analyze_gap(
    "Test Problem",
    "find two numbers that sum to target",
    "def f(nums, t):\n    for i in range(len(nums)):\n        for j in range(len(nums)):\n            if nums[i]+nums[j]==t: return True",
    None,
    similar_submissions=[
        {"problem_title": "Two Sum", "category": "misunderstood_problem", "note": "You compared i to itself."}
    ],
)

print(result)