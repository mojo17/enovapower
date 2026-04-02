"""Shared test fixtures for enovapower tests."""

MINIMAL_CSV = (
    '"Reading Date","1 am kWh Usage","2 am kWh Usage","3 am kWh Usage",'
    '"4 am kWh Usage","5 am kWh Usage","6 am kWh Usage","7 am kWh Usage",'
    '"8 am kWh Usage","9 am kWh Usage","10 am kWh Usage","11 am kWh Usage",'
    '"12 pm kWh Usage","1 pm kWh Usage","2 pm kWh Usage","3 pm kWh Usage",'
    '"4 pm kWh Usage","5 pm kWh Usage","6 pm kWh Usage","7 pm kWh Usage",'
    '"8 pm kWh Usage","9 pm kWh Usage","10 pm kWh Usage","11 pm kWh Usage",'
    '"12 pm kWh Usage","[touInquiry_download_Total_TOU_ON_Peak_Consumption]",'
    '"[touInquiry_download_Total_TOU_MID_Peak_Consumption]",'
    '"[touInquiry_download_Total_TOU_OFF_Peak_Consumption]"\n'
    '"2026-03-01","1.00","2.00","3.00","4.00","5.00","6.00","7.00","8.00",'
    '"9.00","10.00","11.00","12.00","1.00","2.00","3.00","4.00","5.00",'
    '"6.00","7.00","8.00","9.00","10.00","11.00","12.00","1.50","2.50","3.50"\n'
)

TWO_ROW_CSV = MINIMAL_CSV.rstrip("\n") + "\n" + (
    '"2026-03-02","0.50","0.50","0.50","0.50","0.50","0.50","0.50","0.50",'
    '"0.50","0.50","0.50","0.50","0.50","0.50","0.50","0.50","0.50",'
    '"0.50","0.50","0.50","0.50","0.50","0.50","0.50","4.00","4.00","4.00"\n'
)

MULTI_ROW_CSV = (
    '"Reading Date","1 am kWh Usage","2 am kWh Usage","3 am kWh Usage",'
    '"4 am kWh Usage","5 am kWh Usage","6 am kWh Usage","7 am kWh Usage",'
    '"8 am kWh Usage","9 am kWh Usage","10 am kWh Usage","11 am kWh Usage",'
    '"12 pm kWh Usage","1 pm kWh Usage","2 pm kWh Usage","3 pm kWh Usage",'
    '"4 pm kWh Usage","5 pm kWh Usage","6 pm kWh Usage","7 pm kWh Usage",'
    '"8 pm kWh Usage","9 pm kWh Usage","10 pm kWh Usage","11 pm kWh Usage",'
    '"12 pm kWh Usage","[touInquiry_download_Total_TOU_ON_Peak_Consumption]",'
    '"[touInquiry_download_Total_TOU_MID_Peak_Consumption]",'
    '"[touInquiry_download_Total_TOU_OFF_Peak_Consumption]"\n'
    '"2026-02-25","4.00","0.88","0.80","0.72","0.79","0.96","0.69","0.77",'
    '"0.61","0.55","0.47","0.47","0.46","0.46","0.70","0.88","0.67",'
    '"2.27","1.41","0.85","0.95","0.96","0.99","1.06","6.08","3.64","13.65"\n'
    '"2026-02-26","0.83","1.20","1.23","0.69","0.73","0.85","0.70","0.61",'
    '"0.74","0.45","0.46","0.46","0.46","0.53","0.63","0.91","0.53",'
    '"1.72","1.93","1.01","0.91","0.76","1.05","1.04","5.91","3.52","11.00"\n'
    '"2026-02-27","0.68","0.67","0.83","0.71","0.74","0.89","0.74","0.63",'
    '"0.79","0.50","0.46","0.47","0.46","0.47","0.48","0.73","0.48",'
    '"0.63","1.08","0.82","0.78","0.62","0.64","0.91","4.09","3.09","9.03"\n'
)

TARIFF_HTML = (
    "<html><body>"
    "<h5><strong>Ultra-Low Overnight Pricing:"
    " Nov 01, 2025 - Oct 31, 2026</strong></h5>"
    "<table id='pricingTableForULO0'>"
    "<thead><tr><th>Electricity</th>"
    "<th>Price (cents/kWh)</th><th>Weekdays</th></tr></thead>"
    "<tbody>"
    "<tr><td>ULO Lon-peak</td><td>3.90</td>"
    "<td>Every day 11 p.m. - 7 a.m.</td></tr>"
    "<tr><td>ULO Off-peak</td><td>9.80</td>"
    "<td>Weekends and holidays 7 a.m. - 11 p.m.</td></tr>"
    "<tr><td>ULO Mid-peak</td><td>15.70</td>"
    "<td>Weekdays 7 a.m. - 4 p.m. and 9 p.m. to 11 p.m.</td></tr>"
    "<tr><td>ULO On-peak</td><td>39.10</td>"
    "<td>Weekdays 4 p.m. - 9 p.m.</td></tr>"
    "</tbody></table>"
    "<h5><strong>Time-of-Use Pricing:"
    " Nov 01, 2025 - Apr 30, 2026</strong></h5>"
    "<table id='pricingTableForTOU0'>"
    "<thead><tr><th>Electricity</th>"
    "<th>Price (cents/kWh)</th><th>Weekdays</th></tr></thead>"
    "<tbody>"
    "<tr><td>TOU Off-peak</td><td>9.80</td>"
    "<td>Weekends and holidays all day and "
    "Weekdays 7 p.m. - 7 a.m.</td></tr>"
    "<tr><td>TOU Mid-peak</td><td>15.70</td>"
    "<td>Weekdays 11 a.m. - 5 p.m.</td></tr>"
    "<tr><td>TOU On-peak</td><td>20.30</td>"
    "<td>Weekdays 7 a.m. - 11 a.m. and "
    "5 p.m. - 7 p.m.</td></tr>"
    "</tbody></table>"
    "<h5><strong>Tiered Price Plan Pricing:"
    " Nov 01, 2025 - Apr 30, 2026</strong></h5>"
    "<table id='pricingTableForTr0'>"
    "<thead><tr><th>Electricity</th><th>Price</th>"
    "<th>Threshold Start</th><th>Threshold End</th></tr></thead>"
    "<tbody>"
    "<tr><td>Tier 1</td><td>12.0000</td>"
    "<td>0.0</td><td>1000.0</td></tr>"
    "<tr><td>Tier 2</td><td>14.2000</td>"
    "<td>1000.0</td><td>Infinity</td></tr>"
    "</tbody></table>"
    "</body></html>"
)
