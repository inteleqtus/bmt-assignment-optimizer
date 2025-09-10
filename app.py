from flask import Flask, request, jsonify
import pandas as pd
from datetime import datetime
import os

# Import OR-Tools with error handling
try:
    from ortools.linear_solver import pywraplp
    ORTOOLS_AVAILABLE = True
except ImportError:
    ORTOOLS_AVAILABLE = False

app = Flask(__name__)

class UpdatedBMTOptimizer:
    def __init__(self):
        self.ortools_available = ORTOOLS_AVAILABLE
    
    def calculate_final_acuity(self, base_acuity, new_admit, chemo_frequency):
        """Calculate final acuity with adjustments"""
        final_acuity = int(base_acuity)
        
        # Add 1 point for new admissions
        if str(new_admit).upper() == 'Y':
            final_acuity += 1
        
        # Add 1 point for multiple IV chemo in 12 hours
        if str(chemo_frequency).lower() == 'multiple':
            final_acuity += 1
        
        return min(final_acuity, 10)  # Cap at 10
    
    def determine_vesicant_status(self, central_line, iv_medications, chemo_type):
        """Determine if patient is receiving vesicant medications"""
        # Only vesicant if peripheral IV
        if str(central_line).lower() != 'peripheral':
            return False
        
        # Check for vesicant medications
        iv_meds = str(iv_medications).lower()
        vesicant_conditions = [
            'antiarrhythmics' in iv_meds,
            'vasopressors' in iv_meds,
            str(chemo_type).upper() == 'IV'
        ]
        
        return any(vesicant_conditions)
    
    def preprocess_patient_data(self, patients_df):
        """Preprocess patient data with new calculations"""
        processed_patients = patients_df.copy()
        
        for idx, patient in processed_patients.iterrows():
            # Calculate final acuity
            base_acuity = patient.get('Base_Acuity', patient.get('Acuity', 5))
            new_admit = patient.get('New_Admit', 'N')
            chemo_freq = patient.get('Chemo_Frequency', 'Single')
            
            final_acuity = self.calculate_final_acuity(base_acuity, new_admit, chemo_freq)
            processed_patients.at[idx, 'Acuity'] = final_acuity
            
            # Determine vesicant status
            central_line = patient.get('Central_Line', 'none')
            iv_medications = patient.get('IV_Medications', '')
            chemo_type = patient.get('Chemo_Type', 'none')
            
            is_vesicant = self.determine_vesicant_status(central_line, iv_medications, chemo_type)
            processed_patients.at[idx, 'Vesicant'] = 'Y' if is_vesicant else 'N'
        
        return processed_patients
    
    def check_hard_constraints(self, nurse, patient):
        """Updated constraint checking with new parameters"""
        violations = []
        
        # IV Chemo certification requirement
        if str(patient.get('Chemo_Type', '')).upper() == 'IV' and str(nurse.get('Chemo_IV_Cert', '')).upper() != 'Y':
            violations.append("IV chemo requires certification")
        
        # Vesicant handling (requires skill level 2+)
        if str(patient.get('Vesicant', '')).upper() == 'Y' and int(nurse.get('Skill_Level', 1)) < 2:
            violations.append("Vesicant medications need experienced nurse (skill 2+)")
        
        # High acuity patients (8+ still need experienced nurses for complex care)
        if int(patient.get('Acuity', 0)) >= 8 and int(nurse.get('Skill_Level', 1)) < 2:
            violations.append("High acuity (8+) needs experienced nurse")
        
        # CMV constraint: pregnant female nurses cannot take CMV+ patients
        cmv_status = str(patient.get('CMV_Status', 'Unknown')).upper()
        pregnancy_status = str(nurse.get('Pregnancy_Status', 'N')).upper()
        
        if (cmv_status == 'POSITIVE' and pregnancy_status == 'Y'):
            violations.append("CMV+ patient cannot be assigned to pregnant nurse")
        
        return violations
    
    def calculate_assignment_score(self, nurse, patient, config):
        """Calculate assignment score with updated acuity considerations"""
        score = 1  # Base score
        
        # Continuity bonus
        if str(nurse.get('Nurse_ID', '')) == str(patient.get('Last_Nurse', '')):
            score += 10 * config.get('Continuity_Weight', 0.30)
        
        # Geography bonus
        if str(nurse.get('Pod_Pref', '')) == str(patient.get('Pod', '')):
            score += 8 * config.get('Geography_Weight', 0.20)
        elif abs(ord(str(nurse.get('Pod_Pref', 'A'))[0]) - ord(str(patient.get('Pod', 'A'))[0])) == 1:
            score += 4 * config.get('Geography_Weight', 0.20)
        
        # Updated skill-acuity matching for 1-10 scale
        skill = int(nurse.get('Skill_Level', 1))
        acuity = int(patient.get('Acuity', 1))
        
        if skill == 3 and acuity >= 8:  # Expert nurse + high complexity
            score += 12 * config.get('Skill_Weight', 0.40)
        elif skill == 3 and 5 <= acuity <= 7:  # Expert + moderate
            score += 10 * config.get('Skill_Weight', 0.40)
        elif skill == 2 and 4 <= acuity <= 8:  # Intermediate + varied complexity
            score += 10 * config.get('Skill_Weight', 0.40)
        elif skill == 1 and acuity <= 5:  # Novice + lower complexity
            score += 8 * config.get('Skill_Weight', 0.40)
        else:
            # Penalty for poor skill-acuity match
            mismatch = abs(skill * 3 - acuity)
            score -= mismatch * config.get('Skill_Weight', 0.40)
        
        # Vesicant bonus for highly skilled nurses
        if str(patient.get('Vesicant', '')).upper() == 'Y' and skill == 3:
            score += 5 * config.get('Skill_Weight', 0.40)
        
        # New admit consideration (higher priority for experienced nurses)
        if str(patient.get('New_Admit', '')).upper() == 'Y' and skill >= 2:
            score += 3 * config.get('Skill_Weight', 0.40)
        
        return score
    
    def validate_input(self, nurses_df, patients_df):
        """Validate input with updated parameters"""
        errors = []
        
        # Check required nurse columns
        required_nurse_cols = ['Nurse_ID', 'Name', 'Skill_Level', 'Chemo_IV_Cert', 'Max_Patients']
        for col in required_nurse_cols:
            if col not in nurses_df.columns:
                errors.append(f"Missing nurse column: {col}")
        
        # Check required patient columns (updated)
        required_patient_cols = ['Patient_ID', 'Initials', 'Base_Acuity', 'Chemo_Type']
        for col in required_patient_cols:
            if col not in patients_df.columns and col.replace('Base_', '') not in patients_df.columns:
                errors.append(f"Missing patient column: {col}")
        
        # Check unit capacity
        if len(patients_df) > 20:
            errors.append(f"Exceeds unit capacity: {len(patients_df)} > 20 patients")
        
        # Check IV certification balance
        iv_patients = len(patients_df[patients_df['Chemo_Type'].str.upper() == 'IV'])
        iv_nurses = len(nurses_df[nurses_df['Chemo_IV_Cert'].str.upper() == 'Y'])
        if iv_patients > iv_nurses * 2:
            errors.append(f"Insufficient IV certified nurses: {iv_patients} IV patients need {iv_nurses} certified nurses")
        
        return errors
    
    def optimize_assignments(self, nurses_df, patients_df, config):
        """Main optimization with updated parameters"""
        if not self.ortools_available:
            return {"error": "OR-Tools optimization not available"}
        
        try:
            # Preprocess patient data with new calculations
            patients_df = self.preprocess_patient_data(patients_df)
            
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
            
            # HARD CONSTRAINTS
            
            # 1. Each patient assigned to exactly one nurse
            for j in range(len(patients_df)):
                solver.Add(sum(x[i, j] for i in range(len(nurses_df))) == 1)
            
            # 2. Nurse capacity limits
            for i in range(len(nurses_df)):
                max_pts = int(nurses_df.iloc[i].get('Max_Patients', 4))
                solver.Add(sum(x[i, j] for j in range(len(patients_df))) <= max_pts)
            
            # 3. Safety and certification constraints
            blocked_assignments = 0
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                for j in range(len(patients_df)):
                    patient = patients_df.iloc[j]
                    violations = self.check_hard_constraints(nurse, patient)
                    if violations:
                        solver.Add(x[i, j] == 0)
                        blocked_assignments += 1
            
            # 4. IV chemo nurse limit (max 2 per certified nurse)
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                if str(nurse.get('Chemo_IV_Cert', '')).upper() == 'Y':
                    iv_count = sum(x[i, j] for j in range(len(patients_df)) 
                                 if str(patients_df.iloc[j].get('Chemo_Type', '')).upper() == 'IV')
                    solver.Add(iv_count <= 2)
            
            # 5. Unit capacity constraint
            total_assigned = sum(x[i, j] for i in range(len(nurses_df)) for j in range(len(patients_df)))
            solver.Add(total_assigned <= 20)
            
            # OBJECTIVE FUNCTION
            objective = solver.Objective()
            
            for i in range(len(nurses_df)):
                nurse = nurses_df.iloc[i]
                for j in range(len(patients_df)):
                    patient = patients_df.iloc[j]
                    score = self.calculate_assignment_score(nurse, patient, config)
                    objective.SetCoefficient(x[i, j], score)
            
            # Penalty for exceeding ideal 1:3 ratio
            for i in range(len(nurses_df)):
                ideal_count = 3
                total_patients = sum(x[i, j] for j in range(len(patients_df)))
                excess = solver.IntVar(0, 4, f'excess_{i}')
                solver.Add(excess >= total_patients - ideal_count)
                solver.Add(excess >= 0)
                objective.SetCoefficient(excess, -5)
            
            objective.SetMaximization()
            
            # Solve
            solver.SetTimeLimit(30000)
            status = solver.Solve()
            
            if status in [pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE]:
                return self.extract_solution(x, nurses_df, patients_df, solver, config, blocked_assignments)
            else:
                return self.create_fallback_solution(nurses_df, patients_df, config)
                
        except Exception as e:
            return {"error": f"Optimization failed: {str(e)}"}
    
    def extract_solution(self, x, nurses_df, patients_df, solver, config, blocked_assignments):
        """Extract solution with updated patient information"""
        assignments = []
        
        for i in range(len(nurses_df)):
            nurse = nurses_df.iloc[i]
            nurse_patients = []
            total_acuity = 0
            iv_count = 0
            vesicant_count = 0
            
            for j in range(len(patients_df)):
                if x[i, j].solution_value() > 0.5:
                    patient = patients_df.iloc[j]
                    
                    patient_data = {
                        'patient_id': str(patient.get('Patient_ID', '')),
                        'initials': str(patient.get('Initials', '')),
                        'base_acuity': int(patient.get('Base_Acuity', patient.get('Acuity', 1))),
                        'final_acuity': int(patient.get('Acuity', 1)),
                        'chemo': str(patient.get('Chemo_Type', 'none')),
                        'chemo_frequency': str(patient.get('Chemo_Frequency', 'Single')),
                        'chemo_time': str(patient.get('Chemo_Time', '')),
                        'vesicant': str(patient.get('Vesicant', 'N')),
                        'central_line': str(patient.get('Central_Line', 'none')),
                        'iv_medications': str(patient.get('IV_Medications', '')),
                        'isolation': str(patient.get('Isolation', 'none')),
                        'cmv_status': str(patient.get('CMV_Status', 'Unknown')),
                        'new_admit': str(patient.get('New_Admit', 'N')),
                        'continuity': 'Y' if str(nurse.get('Nurse_ID', '')) == str(patient.get('Last_Nurse', '')) else 'N'
                    }
                    
                    nurse_patients.append(patient_data)
                    total_acuity += int(patient.get('Acuity', 1))
                    
                    if str(patient.get('Chemo_Type', '')).upper() == 'IV':
                        iv_count += 1
                    if str(patient.get('Vesicant', '')).upper() == 'Y':
                        vesicant_count += 1
            
            if nurse_patients:
                patient_count = len(nurse_patients)
                assignments.append({
                    'nurse_id': str(nurse.get('Nurse_ID', '')),
                    'nurse_name': str(nurse.get('Name', '')),
                    'role': str(nurse.get('Role', 'RN')),
                    'skill_level': int(nurse.get('Skill_Level', 1)),
                    'pregnancy_status': str(nurse.get('Pregnancy_Status', 'N')),
                    'phone': str(nurse.get('Phone_Number', '')),
                    'patients': nurse_patients,
                    'patient_count': patient_count,
                    'total_acuity': total_acuity,
                    'iv_chemo_count': iv_count,
                    'vesicant_count': vesicant_count,
                    'ratio_status': 'ideal' if patient_count <= 3 else 'maximum',
                    'continuity_count': sum(1 for p in nurse_patients if p['continuity'] == 'Y'),
                    'new_admit_count': sum(1 for p in nurse_patients if p['new_admit'] == 'Y')
                })
        
        # Calculate statistics
        if assignments:
            acuities = [a['total_acuity'] for a in assignments]
            patient_counts = [a['patient_count'] for a in assignments]
            
            stats = {
                'total_patients': len(patients_df),
                'total_nurses_used': len(assignments),
                'unit_capacity_used': f"{sum(patient_counts)}/20",
                'unit_capacity_percentage': round((sum(patient_counts) / 20) * 100, 1),
                'workload_variance': max(acuities) - min(acuities),
                'average_acuity': round(sum(acuities) / len(acuities), 1),
                'ideal_ratios': sum(1 for count in patient_counts if count <= 3),
                'max_ratios': sum(1 for count in patient_counts if count == 4),
                'continuity_preserved': sum(a['continuity_count'] for a in assignments),
                'new_admissions': sum(a['new_admit_count'] for a in assignments),
                'total_iv_chemo': sum(a['iv_chemo_count'] for a in assignments),
                'total_vesicants': sum(a['vesicant_count'] for a in assignments),
                'blocked_assignments': blocked_assignments,
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'objective_value': round(solver.Objective().Value(), 2),
                'solution_time_ms': solver.WallTime()
            }
        else:
            stats = {'error': 'No assignments generated'}
        
        return {
            'success': True,
            'assignments': assignments,
            'stats': stats
        }
    
    def create_fallback_solution(self, nurses_df, patients_df, config):
        """Create fallback solution when optimization fails"""
        assignments = []
        unassigned_patients = patients_df.copy().sort_values('Acuity', ascending=False)
        
        # Initialize nurse workloads
        nurse_workloads = {nurse['Nurse_ID']: {'patients': [], 'acuity': 0} 
                          for _, nurse in nurses_df.iterrows()}
        
        # Assign critical patients first (high acuity, IV chemo)
        for idx, patient in unassigned_patients.iterrows():
            best_nurse = None
            best_score = -999
            
            for _, nurse in nurses_df.iterrows():
                # Check capacity
                if len(nurse_workloads[nurse['Nurse_ID']]['patients']) >= nurse['Max_Patients']:
                    continue
                
                # Check hard constraints
                violations = self.check_hard_constraints(nurse, patient)
                if violations:
                    continue
                
                # Calculate assignment score
                score = self.calculate_assignment_score(nurse, patient, config)
                
                # Prefer less loaded nurses (workload balancing)
                current_acuity = nurse_workloads[nurse['Nurse_ID']]['acuity']
                workload_penalty = current_acuity * 0.3
                total_score = score - workload_penalty
                
                if total_score > best_score:
                    best_score = total_score
                    best_nurse = nurse
            
            if best_nurse is not None:
                nurse_workloads[best_nurse['Nurse_ID']]['patients'].append({
                    'patient_id': patient['Patient_ID'],
                    'initials': patient['Initials'],
                    'final_acuity': patient['Acuity'],
                    'chemo': patient.get('Chemo_Type', 'none'),
                    'vesicant': patient.get('Vesicant', 'N')
                })
                nurse_workloads[best_nurse['Nurse_ID']]['acuity'] += patient['Acuity']
                unassigned_patients = unassigned_patients.drop(idx)
        
        # Convert to output format
        for _, nurse in nurses_df.iterrows():
            workload = nurse_workloads[nurse['Nurse_ID']]
            if workload['patients']:
                assignments.append({
                    'nurse_id': nurse['Nurse_ID'],
                    'nurse_name': nurse['Name'],
                    'patients': workload['patients'],
                    'total_acuity': workload['acuity'],
                    'patient_count': len(workload['patients'])
                })
        
        acuities = [a['total_acuity'] for a in assignments] if assignments else [0]
        
        return {
            'success': True,
            'fallback': True,
            'assignments': assignments,
            'unassigned_patients': len(unassigned_patients),
            'stats': {
                'workload_variance': max(acuities) - min(acuities),
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }

# Flask application routes
@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "Updated BMT Assignment Optimizer",
        "version": "2.0.0",
        "ortools_available": ORTOOLS_AVAILABLE,
        "endpoints": ["/", "/test", "/optimize"],
        "updates": [
            "Updated vesicant definition (peripheral IV + specific medications)",
            "Multiple chemo acuity adjustment (+1 point)", 
            "New admission acuity bonus (+1 point)",
            "1-10 acuity scale with defined levels",
            "CMV pregnancy constraint (pregnant nurses only)",
            "Removed transfusion parameters",
            "Automatic acuity and vesicant calculation"
        ],
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/test', methods=['GET'])
def test_updated_optimization():
    """Test endpoint with updated BMT sample data"""
    if not ORTOOLS_AVAILABLE:
        return jsonify({
            "error": "OR-Tools not available",
            "message": "Optimization functionality disabled"
        }), 500
    
    # Updated sample data with new parameters
    nurses_data = [
        {"Nurse_ID": "N001", "Name": "Johnson, Sarah", "Role": "RN", "Skill_Level": 3, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Pod_Pref": "A", "Pregnancy_Status": "N", "Phone_Number": "+1234567890"},
        {"Nurse_ID": "N002", "Name": "Martinez, Lisa", "Role": "RN", "Skill_Level": 2, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Pod_Pref": "B", "Pregnancy_Status": "N", "Phone_Number": "+1234567891"},
        {"Nurse_ID": "N003", "Name": "Chen, Michael", "Role": "RN", "Skill_Level": 3, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Pod_Pref": "C", "Pregnancy_Status": "N/A", "Phone_Number": "+1234567892"},
        {"Nurse_ID": "N004", "Name": "Williams, Karen", "Role": "RN", "Skill_Level": 2, "Chemo_IV_Cert": "Y", "Max_Patients": 4, "Pod_Pref": "A", "Pregnancy_Status": "Prefer_Not_To_Say", "Phone_Number": "+1234567893"},
        {"Nurse_ID": "N005", "Name": "Brown, James", "Role": "LVN", "Skill_Level": 2, "Chemo_IV_Cert": "N", "Max_Patients": 4, "Pod_Pref": "B", "Pregnancy_Status": "N/A", "Phone_Number": "+1234567894"},
        {"Nurse_ID": "N006", "Name": "Davis, Amanda", "Role": "RN", "Skill_Level": 1, "Chemo_IV_Cert": "N", "Max_Patients": 4, "Pod_Pref": "C", "Pregnancy_Status": "Y", "Phone_Number": "+1234567895"}
    ]
    
    patients_data = [
        {"Patient_ID": "301A", "Initials": "J.D.", "Pod": "A", "Base_Acuity": 7, "New_Admit": "N", "Chemo_Type": "IV", "Chemo_Frequency": "Single", "Chemo_Time": "20:00", "Central_Line": "peripheral", "IV_Medications": "chemo", "Isolation": "contact", "CMV_Status": "Negative", "Last_Nurse": "N001"},
        {"Patient_ID": "302A", "Initials": "M.K.", "Pod": "A", "Base_Acuity": 4, "New_Admit": "N", "Chemo_Type": "oral", "Chemo_Frequency": "Single", "Central_Line": "none", "IV_Medications": "", "Isolation": "none", "CMV_Status": "Negative", "Last_Nurse": "N001"},
        {"Patient_ID": "303A", "Initials": "R.L.", "Pod": "A", "Base_Acuity": 3, "New_Admit": "N", "Chemo_Type": "none", "Chemo_Frequency": "Single", "Central_Line": "none", "IV_Medications": "", "Isolation": "none", "CMV_Status": "Unknown", "Last_Nurse": "N004"},
        {"Patient_ID": "304A", "Initials": "S.B.", "Pod": "A", "Base_Acuity": 5, "New_Admit": "Y", "Chemo_Type": "IV", "Chemo_Frequency": "Multiple", "Chemo_Time": "08:00,20:00", "Central_Line": "PICC", "IV_Medications": "chemo", "Isolation": "neutropenic", "CMV_Status": "Positive", "Last_Nurse": ""},
        {"Patient_ID": "305B", "Initials": "T.M.", "Pod": "B", "Base_Acuity": 8, "New_Admit": "N", "Chemo_Type": "none", "Chemo_Frequency": "Single", "Central_Line": "peripheral", "IV_Medications": "vasopressors", "Isolation": "contact", "CMV_Status": "Positive", "Last_Nurse": "N002"},
        {"Patient_ID": "306B", "Initials": "K.W.", "Pod": "B", "Base_Acuity": 3, "New_Admit": "N", "Chemo_Type": "oral", "Chemo_Frequency": "Single", "Central_Line": "none", "IV_Medications": "", "Isolation": "none", "CMV_Status": "Negative", "Last_Nurse": "N002"},
        {"Patient_ID": "307B", "Initials": "L.P.", "Pod": "B", "Base_Acuity": 6, "New_Admit": "N", "Chemo_Type": "none", "Chemo_Frequency": "Single", "Central_Line": "peripheral", "IV_Medications": "antiarrhythmics", "Isolation": "droplet", "CMV_Status": "Unknown", "Last_Nurse": "N005"},
        {"Patient_ID": "308B", "Initials": "D.F.", "Pod": "B", "Base_Acuity": 2, "New_Admit": "Y", "Chemo_Type": "none", "Chemo_Frequency": "Single", "Central_Line": "none", "IV_Medications": "", "Isolation": "none", "CMV_Status": "Negative", "Last_Nurse": ""}
    ]
    
    config = {
        'Continuity_Weight': 0.30,
        'Skill_Weight': 0.40,
        'Geography_Weight': 0.20,
        'Workload_Balance_Weight': 0.10
    }
    
    # Convert to DataFrames and run optimization
    nurses_df = pd.DataFrame(nurses_data)
    patients_df = pd.DataFrame(patients_data)
    
    optimizer = UpdatedBMTOptimizer()
    result = optimizer.optimize_assignments(nurses_df, patients_df, config)
    
    return jsonify(result)

@app.route('/optimize', methods=['POST'])
def optimize():
    """Production optimization endpoint for n8n integration"""
    if not ORTOOLS_AVAILABLE:
        return jsonify({"error": "OR-Tools not available"}), 500
    
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Extract data from request
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
        optimizer = UpdatedBMTOptimizer()
        result = optimizer.optimize_assignments(nurses_df, patients_df, config)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            "error": f"API error: {str(e)}",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
