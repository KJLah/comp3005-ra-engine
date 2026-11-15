-- non-key join with a selective filter on the small side
SELECT w.eid, p.pname FROM Works w JOIN Proj p ON w.pid = p.pid WHERE p.did = 3 AND w.role = 'lead';