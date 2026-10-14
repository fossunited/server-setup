# deploy/secrets

Files here are mounted read-only into the controller at `/app/secrets`. Everything
except this README is git-ignored.

## youtube-cookies.txt (only if YouTube blocks the server)

If the admin console shows "YouTube is blocking this server" (the "Sign in to
confirm you're not a bot" check), give Streamlink the cookies of a signed-in
throwaway Google account:

1. In a private browser window, sign in to a throwaway Google account and open
   youtube.com.
2. Export cookies for youtube.com in Netscape format (e.g. the "Get cookies.txt
   LOCALLY" extension).
3. Save the file as `deploy/secrets/youtube-cookies.txt` on the server.
4. Restart the affected stream from the admin console (no container restart needed).

Cookies used from a datacenter IP expire quickly; expect to refresh them.
