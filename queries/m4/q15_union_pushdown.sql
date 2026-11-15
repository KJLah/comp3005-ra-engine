-- a select above a union_all in a derived table (rule 4)
SELECT u.city FROM (SELECT city FROM Emp UNION ALL SELECT city FROM Dept) AS u WHERE u.city = 'Ottawa';