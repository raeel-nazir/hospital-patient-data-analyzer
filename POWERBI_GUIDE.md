# Power BI Dashboard Guide

1. Open Power BI Desktop, then Get Data > Text/CSV > select `outputs/cleaned_healthcare.csv` > Load.
2. Check data types in Power Query: Date of Admission and Discharge Date = Date,
   Billing Amount = Decimal number, Length of Stay = Whole number.

## DAX measures
    Total Patients   = COUNTROWS(cleaned_healthcare)
    Avg Billing      = AVERAGE(cleaned_healthcare[Billing Amount])
    Total Billing    = SUM(cleaned_healthcare[Billing Amount])
    Avg Stay         = AVERAGE(cleaned_healthcare[Length of Stay])
    Abnormal %       = DIVIDE(
                         CALCULATE(COUNTROWS(cleaned_healthcare), cleaned_healthcare[Test Results] = "Abnormal"),
                         COUNTROWS(cleaned_healthcare))

## Page layout
- Top row, 4 cards: Total Patients, Avg Age, Avg Stay, Avg Billing.
- Bar chart: Patients by Medical Condition.
- Bar chart: Avg Billing by Medical Condition.
- Column chart: Patients by Admission Type.
- Bar chart: Total Billing by Insurance Provider.
- Column chart: Patients by Age Group.
- Donut chart: Test Results.
- Line chart: Patients by Admission Month.
- Slicers: Medical Condition, Admission Type, Insurance Provider, Gender, Admission Year.
