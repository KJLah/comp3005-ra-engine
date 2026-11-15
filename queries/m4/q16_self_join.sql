-- self join through two renames
SELECT a.dname, b.dname FROM Dept a JOIN Dept b ON a.city = b.city WHERE a.did < b.did;