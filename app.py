# app.py - BMT Assignment Optimizer API for Railway.app
from flask import Flask, request, jsonify
from ortools.linear_solver import pywraplp
import pandas as pd
from datetime import datetime
import os

app = Flask(__name__)

class BMTOptimizer:
    def __init__(self):
        pass
    
    def validate_input(self, nurses_df, patients_df):
        """Validate input data before optimization"""
        errors = []
        
        # Check required nurse columns
        required_nurse_cols = ['Nurse_ID', 'Name', 'Skill_Level', 'Chemo_IV_Cert', 'Max_Patients']
        for col in required_nurse_cols:
            if col not in nurses_df.columns:
                errors.append(f"Missing nurse column: {col}")
        
        # Check required patient columns
        required_patient_cols = ['Patient_ID', 'Initials', 'Acuity', 'Chemo_Type']
        for col in required_patient_cols:
            if col not in patients_df.columns:
                errors.append(f"Missing patient column: {col}")
        
        # Check capacity
        if len(patients_df) > 20:
            errors.append(f"Too many patients: {len(patients_df)} > 20 unit capacity")
        
        # Check IV certification balance
        iv_patients = len(patients_df[patients_df['Chemo_Type'] == 'IV'])
        iv_nurses = len(nurses_df[nurses_df['Chemo_IV_Cert'] == 'Y'])
        if iv_patients > iv_nurses * 2:
            errors.append(f"Insufficient IV certified nurses: {iv_patients} patients need {iv_nurses} nurses")
        
        return errors
    
    def check_constraints(self, nurse, patient):
        """Check hard constraints"""
        violations = []
        
        if patient['Chemo_Type'] == 'IV' and nurse['Chemo_IV_Cert'] != 'Y':
            violations.append("IV chemo requires certification")
        
        if patient.get('Vesicant') == 'Y' and nurse['Skill_Level'] < 2:
            violations.append("Vesicant needs experienced nurse")
        
        if patient['Acuity'] >= 8 and nurse['Skill_Level'] < 2:
            violations.append("High acuity needs experienced nurse")
        
        return violations
    
    def calculate_score(self, nurse, patient, config):
        """Calculate assignment score"""
        score = 0
        
        # Continuity
        if str(nurse.get('Nurse_ID', '')) == str(patient.get('Last_Nurse', '')):
            score += 10 * config.get('Continuity_Weight', 0.3)
        
        # Geography
        if nurse.get('Pod_Pref') == patient.get('Pod'):
            score += 8 * config.get('Geography_Weight', 0.2)
        
        # Skill matching
        skill = nurse.get('Skill_Level', 1)
        acuity = patient.get('Acuity', 1)
        
        if skill == 3 and acuity >= 7:
            score += 10 * config.get('Skill_Weight', 0.4)
        elif skill == 2 and 4 <= acuity <= 7:
            score += 8 * config.get('Skill_Weight', 0.4)
        elif skill == 1 and acuity <= 4:
            score += 6 * config.get('Skill_Weight', 0.4)
        
        return score
    
    def optimize_assignments(self, nurses_df, patients_df, config):
        """Main optimization function"""
        try:
            # Validate input
            errors = self.validate_input(nurses_df, patients_df)
            if errors:
                return {"error": "Validation failed", "details": errors}
            
            # Create solver
            solver = pywraplp.Solver.CreateSolver('SCIP')
            if not solver:
                return {"error": "Could not create optimization solver"}
            
            # Decision variables
            x = {}
            for i in range(len(nurses_df)):
                for j in range(len(patients_df)):
                    x[i, j] = solver.IntVar(0, 1, f'x_{i}_{j}')
            
            # Constraints
            # 1. Each patient assigned once
            for j in range(len(patients_df)):
                solver.Add(sum(x[i, j] for i in range(len(nurses_df))) == 1)
            
            # 2. Capacity limits
            for i in range(len(nurses_df)):
                max_pts = nurses_df.iloc[i].get('Max_Patients', 4)
                solver.Add(sum(x[i, j] for j in range(len(patients_df))) <= max_pts)
            
            # 3. Safety constraints
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                for j in range(len(patients_df)):
                    patient = patients_df.iloc[j]
                    violations = self.check_constraints(nurse, patient)
                    if violations:
                        solver.Add(x[i, j] == 0)
            
            # 4. IV chemo limit (max 2 per certified nurse)
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                if nurse.get('Chemo_IV_Cert') == 'Y':
                    iv_count = sum(x[i, j] for j in range(len(patients_df)) 
                                 if patients_df.iloc[j]['Chemo_Type'] == 'IV')
                    solver.Add(iv_count <= 2)
            
            # 5. Unit capacity (max 20 patients)
            total_assigned = sum(x[i, j] for i in range(len(nurses_df)) for j in range(len(patients_df)))
            solver.Add(total_assigned <= 20)
            
            # Objective function
            objective = solver.Objective()
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                for j in range(len(patients_df)):
                    patient = patients_df.iloc[j]
                    score = self.calculate_score(nurse, patient, config)
                    objective.SetCoefficient(x[i, j], score)
            
            # Penalty for exceeding ideal 1:3 ratio
            for i in range(len(nurses_df)):
                ideal_count = 3
                total_patients = sum(x[i, j] for j in range(len(patients_df)))
                excess = solver.IntVar(0, 4, f'excess_{i}')
                solver.Add(excess >= total_patients - ideal_count)
                objective.SetCoefficient(excess, -5)
            
            objective.SetMaximization()
            
            # Solve with timeout
            solver.SetTimeLimit(30000)  # 30 seconds max
            status = solver.Solve()
            
            if status in [pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE]:
                return self.extract_solution(x, nurses_df, patients_df, solver)
            else:
                return {"error": "No feasible solution found", "status": status}
                
        except Exception as e:
            return {"error": f"Optimization failed: {str(e)}"}
    
    def extract_solution(self, x, nurses_df, patients_df, solver):
        """Extract and format the solution"""
        assignments = []
        
        for i in range(len(nurses_df)):
            nurse = nurses_df.iloc[i]
            nurse_patients = []
            total_acuity = 0
            iv_count = 0
            
            for j in range(len(patients_df)):
                if x[i, j].solution_value() > 0.5:
                    patient = patients_df.iloc[j]
                    nurse_patients.append({
                        'patient_id': patient['Patient_ID'],
                        'initials': patient['Initials'],
                        'acuity': patient['Acuity'],
                        'chemo': patient.get('Chemo_Type', 'none'),
                        'chemo_time': patient.get('Chemo_Time', ''),
                        'continuity': 'Y' if str(nurse['Nurse_ID']) == str(patient.get('Last_Nurse', '')) else 'N'
                    })
                    total_acuity += patient['Acuity']
                    if patient.get('Chemo_Type') == 'IV':
                        iv_count += 1
            
            if nurse_patients:
                patient_count = len(nurse_patients)
                assignments.append({
                    'nurse_id': nurse['Nurse_ID'],
                    'nurse_name': nurse['Name'],
                    'skill_level': nurse.get('Skill_Level', 1),
                    'phone': nurse.get('Phone_Number', ''),
                    'patients': nurse_patients,
                    'patient_count': patient_count,
                    'total_acuity': total_acuity,
                    'iv_chemo_count': iv_count,
                    'ratio_status': 'ideal' if patient_count <= 3 else 'maximum',
                    'continuity_count': sum(1 for p in nurse_patients if p['continuity'] == 'Y')
                })
        
        # Calculate metrics
        if assignments:
            acuities = [a['total_acuity'] for a in assignments]
            patient_counts = [a['patient_count'] for a in assignments]
            
            stats = {
                'total_patients': len(patients_df),
                'total_nurses_used': len(assignments),
                'unit_capacity_used': f"{sum(patient_counts)}/20",
                'workload_variance': max(acuities) - min(acuities),
                'average_acuity': round(sum(acuities) / len(acuities), 1),
                'ideal_ratios': sum(1 for count in patient_counts if count <= 3),
                'max_ratios': sum(1 for count in patient_counts if count == 4),
                'continuity_preserved': sum(a['continuity_count'] for a in assignments),
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'objective_value': solver.Objective().Value()
            }
        else:
            stats = {'error': 'No assignments generated'}
        
        return {
            'success': True,
            'assignments': assignments,
            'stats': stats
        }

# Flask routes
@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "BMT Assignment Optimizer",
        "version": "1.0.0",
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/optimize', methods=['POST'])
def optimize():
    """Main optimization endpoint"""
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Extract data
        nurses_data = data.get('nurses', [])
        patients_data = data.get('patients', [])
        config = data.get('config', {
            'Continuity_Weight': 0.30,
            'Skill_Weight': 0.40,
            'Geography_Weight': 0.20,
            'Workload_Balance_Weight': 0.10
        })
        
        if not nurses_data or not patients_data:
            return jsonify({"error": "Missing nurses or patients data"}), 400
        
        # Convert to DataFrames
        nurses_df = pd.DataFrame(nurses_data)
        patients_df = pd.DataFrame(patients_data)
        
        # Run optimization
        optimizer = BMTOptimizer()
        result = optimizer.optimize_assignments(nurses_df, patients_df, config)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            "error": f"API error: {str(e)}",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }), 500

@app.route('/test', methods=['GET'])
def test_with_sample_data():
    """Test endpoint with sample data"""
    # Sample data for testing
    nurses_data = [
        {"Nurse_ID": "N001", "Name": "Johnson, Sarah", "Skill_Level": 3, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Phone_Number": "+1234567890"},
        {"Nurse_ID": "N002", "Name": "Martinez, Lisa", "Skill_Level": 2, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Phone_Number": "+1234567891"},
        {"Nurse_ID": "N003", "Name": "Chen, Michael", "Skill_Level": 3, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Phone_Number": "+1234567892"}
    ]
    
    patients_data = [
        {"Patient_ID": "301A", "Initials": "J.D.", "Acuity": 8, "Chemo_Type": "IV", "Last_Nurse": "N001"},
        {"Patient_ID": "302A", "Initials": "M.K.", "Acuity": 5, "Chemo_Type": "oral", "Last_Nurse": "N001"},
        {"Patient_ID": "303A", "Initials": "R.L.", "Acuity": 3, "Chemo_Type": "none", "Last_Nurse": "N002"},
        {"Patient_ID": "304A", "Initials": "S.B.", "Acuity": 6, "Chemo_Type": "IV", "Last_Nurse": "N002"},
        {"Patient_ID": "305B", "Initials": "T.M.", "Acuity": 9, "Chemo_Type": "IV", "Last_Nurse": "N003"}
    ]
    
    config = {
        'Continuity_Weight': 0.30,
        'Skill_Weight': 0.40,
        'Geography_Weight': 0.20,
        'Workload_Balance_Weight': 0.10
    }
    
    # Run optimization
    nurses_df = pd.DataFrame(nurses_data)
    patients_df = pd.DataFrame(patients_data)
    
    optimizer = BMTOptimizer()
    result = optimizer.optimize_assignments(nurses_df, patients_df, config)
    
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)