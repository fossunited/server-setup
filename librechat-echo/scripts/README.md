## Scripts for generating usage report

Monthly usage reports for `Echo` need to be generated for analysis of credit spends and billing. The data is stored in two MongoDB collections: users and transactions. 

By running the parseJSON.py script on JSON exports from these collections over a specified period, the output is transformed into a cleaner JSON format containing only the relevant fields and data necessary for the report.

This cleaned JSON data can then be converted into CSV or XLSX formats for further modifications and analysis.

### Additional context for usage reports

#### Token Types 				
Tokens executed are mainly two in number: 				
1. Prompts (input)
2. Completions (output)

To know more, refer [here](https://subscription.packtpub.com/book/data/9781800563193/2/ch02lvl1sec06/understanding-prompts-completions-and-tokens).


`rawAmount` = Raw amount of tokens as counted per the tokenizer algorithm.	

`rate` = The rate at which tokens are charged as credits.		

`tokenAmount` = Token credits value. 1000 credits = $0.001 (1 mill USD)				
				
#### Rates for Models				
				
| Model               | Prompt | Completion |
|---------------------|--------|------------|
| gpt-4               | 30     | 60         |
| gpt-3.5-turbo       | 1.5    | 2          |
| gpt-4-turbo-preview | 10     | 30         |
| gpt-3.5-turbo-0125  | 0.5    | 1.5        |
| gpt-4o              | 5      | 15         |
| gpt-4o-mini         | 0.15   | 0.6        |
				
				
### Formulae				
				
`Token Amount:` 

$$
\text{tokenAmount} = \text{rawAmount} \times \text{rate}
$$

`Credit Spend:`

$$
\text{creditSpend} = \frac{\text{tokenValue}}{1,000,000}
$$
