"""Демо-приглашения должны действительно приниматься системой."""
from app.models import Class, InviteCode

DEMO_CLASSES = ("8А", "8Б", "4Б")
DEMO_TEACHER_CODES = {"DEMO-TEACHER-2", "DEMO-TEACHER-3"}


def test_demo_class_invite_codes_are_accepted(client, real_db):
    classes = (
        real_db.query(Class).filter(Class.name.in_(DEMO_CLASSES)).all()
    )
    assert len(classes) == len(DEMO_CLASSES)
    for class_ in classes:
        code = class_.invite_code
        # Ввод приводится к upper(): строчные буквы и "-"/"_" делают код
        # недостижимым.
        assert code == code.upper()
        assert code.isalnum()

        info = client.get(
            "/api/users/invite-code", params={"code": code.lower()}
        )
        assert info.status_code == 200
        assert info.json() == {
            "valid": True,
            "role": "student",
            "class_name": class_.name,
        }


def test_demo_teacher_invite_codes_are_accepted(client, real_db):
    codes = {
        row.code
        for row in real_db.query(InviteCode).filter(
            InviteCode.code.like("DEMO-TEACHER-%")
        )
    }
    assert codes == DEMO_TEACHER_CODES

    for code in codes:
        info = client.get(
            "/api/users/invite-code", params={"code": code}
        )
        assert info.status_code == 200
        assert info.json() == {
            "valid": True,
            "role": "teacher",
            "class_name": None,
        }
