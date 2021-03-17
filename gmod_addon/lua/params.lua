CreateConVar("relay_connection", "localhost:8080", nil, "Connection string of the relay server (will have http:// prepended automatically)")
CreateConVar("relay_postinterval", 16, nil, "How many ticks to wait between cache POSTs", 1)