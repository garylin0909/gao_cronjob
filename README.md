# GAO Cronjob

GitHub Actions scheduled task for Gun Art Online mining.

## Schedule

Runs once every 6 hours and can also be started manually from the Actions tab.

## Required secrets

- `GAO_USERNAME`
- `GAO_PASSWORD`

## Optional variables

- `GAO_MINE_ZONE`: default is `iron_mine`
- `GAO_SAFE_THRESHOLD`: default is `0.70`
- `GAO_MAX_FOOD_USES`: default is `10`
- `GAO_FOOD_NAME`: set to `牛肉` if you want to only eat beef
