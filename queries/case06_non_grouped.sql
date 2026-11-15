-- Required case 6 (error): name is not a grouping column
SELECT did, name, COUNT(*) FROM Emp GROUP BY did;