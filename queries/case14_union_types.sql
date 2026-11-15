-- Required case 14 (error): VARCHAR against INT in a UNION
SELECT name FROM Emp UNION SELECT did FROM Dept;