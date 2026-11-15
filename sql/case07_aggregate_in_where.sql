-- Required case 7 (error): an aggregate in WHERE
SELECT did FROM Emp WHERE COUNT(*) > 3 GROUP BY did;