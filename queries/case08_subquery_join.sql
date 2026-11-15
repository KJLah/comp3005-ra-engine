-- Required case 8: a subquery in FROM joined to a base table
SELECT d.title, t.n
  FROM Dept d JOIN (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t ON d.did = t.did;