"""KYC state machine. Mirrors `claims/states.py`: every move is checked here, and nothing
moves a verification into REJECTED except a named reviewer (there is no automatic rejection)."""

from app.models import KycState

TRANSITIONS: dict[str, set[str]] = {
    KycState.SUBMITTED: {KycState.PROCESSING},
    KycState.PROCESSING: {
        KycState.NEEDS_INFORMATION,
        KycState.NEEDS_REVIEW,
        KycState.VERIFIED,
    },
    KycState.NEEDS_INFORMATION: set(),  # the subject submits a new verification
    KycState.NEEDS_REVIEW: {KycState.VERIFIED, KycState.REJECTED, KycState.NEEDS_INFORMATION},
    KycState.VERIFIED: {KycState.EXPIRED, KycState.REJECTED},  # a reviewer may revoke
    KycState.REJECTED: set(),
    KycState.EXPIRED: set(),
}
REVIEWABLE = {KycState.NEEDS_REVIEW}
REVIEWER_OUTCOMES = {KycState.VERIFIED, KycState.REJECTED, KycState.NEEDS_INFORMATION}


class IllegalTransition(Exception):
    pass


def check_transition(src: str, dst: str) -> None:
    if dst not in TRANSITIONS.get(src, set()):
        raise IllegalTransition(f"{src} -> {dst} is not allowed")


# what the subject is told; the reason codes themselves are for reviewers
REASON_TEXT = {
    "CAPTURE_TOO_SMALL": "The photo is too small. Take it closer, in good light.",
    "CAPTURE_BLURRY": "The photo is blurry. Hold the camera steady and retake it.",
    "CAPTURE_GLARE": "There is glare on the document. Tilt it slightly and retake the photo.",
    "NO_FACE_IN_SELFIE": "We could not see your face in the selfie. Retake it facing the camera.",
    "NO_FACE_ON_ID": (
        "We could not find the photo on your document. Retake it showing the whole card."
    ),
    "SELFIE_TOO_SMALL": "The selfie is too small. Retake it closer, in good light.",
}
DEFAULT_REVIEW_TEXT = "Your documents are with a reviewer, who will confirm shortly."
