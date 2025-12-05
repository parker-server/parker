from app.main import app
from app.api.deps import get_current_user
from app.models.library import Library


def test_admin_can_create_library(admin_client, db):
    """Test that an admin can create a library via API"""
    payload = {"name": "Marvel Comics", "path": "/data/marvel"}

    response = admin_client.post("/api/libraries/", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Marvel Comics"
    assert data["id"] is not None

    # Verify DB
    lib = db.query(Library).first()
    assert lib.name == "Marvel Comics"


def test_user_rls_security(client, db, admin_user, normal_user):
    """
    Complex Scenario:
    1. Admin creates a Library.
    2. Admin CAN see it.
    3. Regular User (not assigned) CANNOT see it.
    """
    # 1. Setup Data directly in DB (faster than API calls)
    lib = Library(name="Secret Library", path="/tmp")
    db.add(lib)
    db.commit()

    # ACT AS ADMIN
    # Manually override the dependency to be the Admin
    app.dependency_overrides[get_current_user] = lambda: admin_user


    # 2. Admin Check
    resp_admin = client.get("/api/libraries/")
    assert resp_admin.status_code == 200
    assert len(resp_admin.json()) == 1

    # ACT AS USER
    # Manually override the dependency to be the Normal User
    app.dependency_overrides[get_current_user] = lambda: normal_user

    # 3. User Check (Should be empty list due to RLS)
    resp_user = client.get("/api/libraries/")
    assert resp_user.status_code == 200
    assert len(resp_user.json()) == 0