-- Required case 1: self join, two renames over one table
SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid;