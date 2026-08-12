def test_health_check(client):
    """Ensure the app is running and health endpoint returns 200"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "parker"}

def test_unauthorized_access(client):
    """Ensure API endpoints reject unauthenticated users"""
    # Try to access libraries without logging in
    response = client.get("/api/libraries/")
    # Should be 401 Unauthorized
    assert response.status_code == 401