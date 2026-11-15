-- three relations: Emp, Works, Proj
SELECT e.name, p.pname, w.hours FROM Emp e JOIN Works w ON e.eid = w.eid JOIN Proj p ON w.pid = p.pid WHERE p.budget > 90000;