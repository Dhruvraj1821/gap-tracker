from auth import hash_password, verify_password, create_access_token, decode_token

def test_password_roundtrip():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed)
    assert not verify_password("wrongpassword", hashed)

def test_token_roundtrip():
    token = create_access_token(user_id=42)
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"