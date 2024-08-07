## Scripts for generating usage report

Monthly usage reports for `Echo` need to be generated for analysis of credit spends and billing. The data is stored in two MongoDB collections: users and transactions. 

By running the parseJSON.py script on JSON exports from these collections over a specified period, the output is transformed into a cleaner JSON format containing only the relevant fields and data necessary for the report.

This cleaned JSON data can then be converted into CSV or XLSX formats for further modifications and analysis.
