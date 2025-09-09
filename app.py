from flask import Flask, request, jsonify
import pandas as pd
from datetime import datetime
import os

app = Flask(__name__)

# Import OR-Tools with error handling
try:
    from ortools.linear_solver import pywraplp
    ORTOOLS_AVAILABLE = True
except ImportError:
    ORTOOLS_AVAILABLE = False
    print("OR-Tools not available, optimization disabled")

class BMTOptimizer:
    def __init__(self):
        self.ortools_available = ORTOOLS_AVAILABLE
    
    def optimize_assignments(self, nurses_df, patients_df, config):
        """Main optimization function with error handling"""
        if not self.ortools_available:
            return {"error": "OR-Tools optimization not available"}
        
        try:
            # Validate input data
            if len(patients_df) == 0 or len(nurses_df) == 0:
                return {"error": "No nurses or patients provided"}
            
            if len(patients_df) > 20:
                return {"error": f"Too many patients: {len(patients_df)} > 20 unit capacity"}
            
            # Create solver
            solver = pywraplp.Solver.CreateSolver('SCIP')
            if not solver:
                return {"error": "Could not create optimization solver"}
            
            # Decision variables
            x = {}
            for i in range(len(nurses_df)):
                for j in range(len(patients_df)):
                    x[i, j] = solver.IntVar(0, 1, f'x_{i}_{j}')
            
            # Constraint 1: Each patient assigned exactly once
            for j in range(len(patients_df)):
                solver.Add(sum(x[i, j] for i in range(len(nurses_df))) == 1)
            
            # Constraint 2: Nurse capacity limits
            for i in range(len(nurses_df)):
                max_pts = int(nurses_df.iloc[i].get('Max_Patients', 4))
                solver.Add(sum(x[i, j] for j in range(len(patients_df))) <= max_pts)
            
            # Constraint 3: IV chemo certification
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                for j in range(len(patients_df)):
                    patient = patients_df.iloc[j]
                    
                    # Block IV chemo for non-certified nurses
                    if (str(patient.get('Chemo_Type', '')).upper() == 'IV' and 
                        str(nurse.get('Chemo_IV_Cert', '')).upper() != 'Y'):
                        solver.Add(x[i, j] == 0)
            
            # Constraint 4: IV chemo limit (max 2 per certified nurse)
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                if str(nurse.get('Chemo_IV_Cert', '')).upper() == 'Y':
                    iv_count = sum(x[i, j] for j in range(len(patients_df)) 
                                 if str(patients_df.iloc[j].get('Chemo_Type', '')).upper() == 'IV')
                    solver.Add(iv_count <= 2)
            
            # Objective function - simple scoring
            objective = solver.Objective()
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                for j in range(len(patients_df)):
                    patient = patients_df.iloc[j]
                    
                    score = 1  # Base score
                    
                    # Continuity bonus
                    if str(nurse.get('Nurse_ID', '')) == str(patient.get('Last_Nurse', '')):
                        score += 10
                    
                    # Skill match bonus
                    skill = int(nurse.get('Skill_Level', 1))
                    acuity = int(patient.get('Acuity', 1))
                    
                    if skill == 3 and acuity >= 7:
                        score += 8
                    elif skill == 2 and 4 <= acuity <= 7:
                        score += 6
                    elif skill == 1 and acuity <= 4:
                        score += 4
                    
                    objective.SetCoefficient(x[i, j], score)
            
            objective.SetMaximization()
            
            # Solve with timeout
            solver.SetTimeLimit(30000)  # 30 seconds
            status = solver.Solve()
            
            if status in [pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE]:
                return self.extract_solution(x, nurses_df, patients_df, solver)
            else:
                return {"error": f"No feasible solution found (status: {status})"}
                
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
                        'patient_id': str(patient.get('Patient_ID', '')),
                        'initials': str(patient.get('Initials', '')),
                        'acuity': int(patient.get('Acuity', 1)),
                        'chemo': str(patient.get('Chemo_Type', 'none')),
                        'continuity': 'Y' if str(nurse.get('Nurse_ID', '')) == str(patient.get('Last_Nurse', '')) else 'N'
                    })
                    total_acuity += int(patient.get('Acuity', 1))
                    if str(patient.get('Chemo_Type', '')).upper() == 'IV':
                        iv_count += 1
            
            if nurse_patients:
                assignments.append({
                    'nurse_id': str(nurse.get('Nurse_ID', '')),
                    'nurse_name': str(nurse.get('Name', '')),
                    'skill_level': int(nurse.get('Skill_Level', 1)),
                    'phone': str(nurse.get('Phone_Number', '')),
                    'patients': nurse_patients,
                    'patient_count': len(nurse_patients),
                    'total_acuity': total_acuity,
                    'iv_chemo_count': iv_count,
                    'ratio_status': 'ideal' if len(nurse_patients) <= 3 else 'maximum'
                })
        
        # Calculate stats
        if assignments:
            acuities = [a['total_acuity'] for a in assignments]
            patient_counts = [a['patient_count'] for a in assignments]
            
            stats = {
                'total_patients': len(patients_df),
                'nurses_used': len(assignments),
                'unit_capacity': f"{sum(patient_counts)}/20",
                'workload_variance': max(acuities) - min(acuities),
                'ideal_ratios': sum(1 for count in patient_counts if count <= 3),
                'max_ratios': sum(1 for count in patient_counts if count == 4),
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
        "version": "1.0.1",
        "ortools_available": ORTOOLS_AVAILABLE,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/test', methods=['GET'])
def test_optimization():
    """Test endpoint with sample data"""
    if not ORTOOLS_AVAILABLE:
        return jsonify({
            "error": "OR-Tools not available",
            "message": "Optimization functionality disabled"
        }), 500
    
    # Sample test data
    nurses_data = [
        {"Nurse_ID": "N001", "Name": "Johnson, Sarah", "Skill_Level": 3, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Phone_Number": "+1234567890"},
        {"Nurse_ID": "N002", "Name": "Martinez, Lisa", "Skill_Level": 2, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Phone_Number": "+1234567891"},
        {"Nurse_ID": "N003", "Name": "Chen, Michael", "Skill_Level": 3, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Phone_Number": "+1234567892"},
        {"Nurse_ID": "N004", "Name": "Brown, James", "Skill_Level": 2, "Chemo_IV_Cert": "N", "Max_Patients": 4, "Phone_Number": "+1234567893"}
    ]
    
    patients_data = [
        {"Patient_ID": "301A", "Initials": "J.D.", "Acuity": 8, "Chemo_Type": "IV", "Last_Nurse": "N001"},
        {"Patient_ID": "302A", "Initials": "M.K.", "Acuity": 5, "Chemo_Type": "oral", "Last_Nurse": "N001"},
        {"Patient_ID": "303A", "Initials": "R.L.", "Acuity": 3, "Chemo_Type": "none", "Last_Nurse": "N004"},
        {"Patient_ID": "304A", "Initials": "S.B.", "Acuity": 6, "Chemo_Type": "IV", "Last_Nurse": "N002"},
        {"Patient_ID": "305B", "Initials": "T.M.", "Acuity": 9, "Chemo_Type": "IV", "Last_Nurse": "N003"},
        {"Patient_ID": "306B", "Initials": "K.W.", "Acuity": 4, "Chemo_Type": "oral", "Last_Nurse": "N002"}
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

@app.route('/optimize', methods=['POST'])
def optimize():
    """Main optimization endpoint for n8n integration"""
    if not ORTOOLS_AVAILABLE:
        return jsonify({"error": "OR-Tools not available"}), 500
    
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        nurses_data = data.get('nurses', [])
        patients_data = data.get('patients', [])
        config = data.get('config', {})
        
        if not nurses_data or not patients_data:
            return jsonify({"error": "Missing nurses or patients data"}), 400
        
        nurses_df = pd.DataFrame(nurses_data)
        patients_df = pd.DataFrame(patients_data)
        
        optimizer = BMTOptimizer()
        result = optimizer.optimize_assignments(nurses_df, patients_df, config)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": f"API error: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
@app.route('/test')
def simple_test():
    return jsonify({
        "message": "Test endpoint working",
        "sample_assignment": {
            "nurse": "Johnson, Sarah",
            "patients": ["301A", "302A"],
            "total_acuity": 13
        }
    })
