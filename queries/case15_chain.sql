-- Required case 15: a four table chain, with commas and with JOIN ON
SELECT e.name, p.pname FROM Emp e, Works w, Proj p, Dept d
 WHERE e.eid = w.eid AND w.pid = p.pid AND p.did = d.did AND d.budget > 10;
SELECT e.name, p.pname FROM Emp e JOIN Works w ON e.eid = w.eid
  JOIN Proj p ON w.pid = p.pid JOIN Dept d ON p.did = d.did
 WHERE d.budget > 10;