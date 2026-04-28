from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import RecommendationFeedback

router = APIRouter()

@router.get("/feedback-ui", response_class=HTMLResponse)
def feedback_form():
    return """
    <html><body>
    <h2>Manual Feedback for Pipeline Recommendation</h2>
    <form action="/feedback-ui" method="post">
      Run ID: <input type="text" name="run_id"><br>
      Vote: <select name="vote">
        <option value="keep">Keep</option>
        <option value="remove">Remove</option>
      </select><br>
      Comment (job/step name): <input type="text" name="comment"><br>
      Actor: <input type="text" name="actor" value="user"><br>
      <input type="submit" value="Submit">
    </form>
    </body></html>
    """

@router.post("/feedback-ui", response_class=HTMLResponse)
def submit_feedback(run_id: str = Form(...), vote: str = Form(...), comment: str = Form(...), actor: str = Form(...), db: Session = Depends(get_db)):
    db.add(RecommendationFeedback(run_id=run_id, vote=vote, comment=comment, actor=actor))
    db.commit()
    return RedirectResponse("/feedback-ui", status_code=303)
