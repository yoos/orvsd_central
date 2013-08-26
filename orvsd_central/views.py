from flask import (request, render_template, flash, g, session, redirect,
                   url_for, abort, jsonify)
from flask.ext.login import (login_required, login_user, logout_user,
                             current_user)
from werkzeug import check_password_hash, generate_password_hash
from orvsd_central import db, app, login_manager, google, celery
from forms import (LoginForm, AddDistrict, AddSchool, AddUser,
                   InstallCourse, AddCourse)
from models import (District, School, Site, SiteDetail,
                    Course, CourseDetail, User)
from sqlalchemy import func, and_
from sqlalchemy.sql.expression import desc
from models import (District, School, Site, SiteDetail,
                    Course, CourseDetail, User)
from bs4 import BeautifulSoup as Soup
import urllib2
import celery
import os
from celery.utils.encoding import safe_repr, safe_str
import json
import re
import subprocess
import StringIO
import requests
import zipfile
import datetime


"""
ACCESS
"""


@app.route("/register", methods=['GET', 'POST'])
#@login_required
def register():
    #user=current_user
    form = AddUser()
    message = ""

    if request.method == "POST":
        if form.password.data != form.confirm_pass.data:
            message = "The passwords provided did not match!\n"
        elif not re.match('^[a-zA-Z0-9._%-]+@[a-zA-Z0-9._%-]+.[a-zA-Z]{2,6}$',
                          form.email.data):
            message = "Invalid email address!\n"
        else:
            # Add user to db
            db.session.add(User(name=form.user.data,
                                email=form.email.data,
                                password=form.password.data))
            db.session.commit()
            message = form.user.data+" has been added successfully!\n"

    return render_template('add_user.html', form=form,
                           message=message, user=current_user)


@app.route("/login", methods=['GET', 'POST'])
def login():
    form = LoginForm(csrf_enabled=False)
    if form.validate_on_submit():
        # login and validate the user...
        user = User.query.filter_by(name=form.name.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash("Logged in successfully.")
            return redirect("/report")
        else:
            flash("Username/Password combo was not recognized.  "
                  "Please try again.")
    return render_template("login.html", form=form)


@app.route("/google_login")
def google_login():
    access_token = session.get('access_token')
    if access_token is None:
        callback = url_for('authorized', _external=True)
        return google.authorize(callback=callback)
    else:
        access_token = access_token
        headers = {'Authorization': 'OAuth '+access_token}
        req = urllib2.Request('https://www.googleapis.com/oauth2/v1/userinfo',
                              None, headers)
        try:
            res = urllib2.urlopen(req)
        except urllib2.URLError, e:
            if e.code == 401:
                session.pop('access_token', None)
                flash('There was a problem with your Google \
                      login information.  Please try again.')
                return redirect(url_for('login'))
            return res.read()
        obj = json.loads(res.read())
        email = obj['email']
        user = User.query.filter_by(email=email).first()
        #pop access token so it isn't sitting around in our
        #session any longer than nescessary
        session.pop('access_token', None)
        if user is not None:
            login_user(user)
            return redirect(url_for('report'))
        else:
            flash("This google account was not recognized \
                  as having access. Sorry.")
            return redirect(url_for('login'))


@app.route(app.config['REDIRECT_URI'])
@google.authorized_handler
def authorized(resp):
    access_token = resp['access_token']
    session['access_token'] = access_token
    return redirect(url_for('google_login'))


@app.route('/me')
@login_required
def home():
    """
    Loads a users home information page
    """
    #not sure current_user works this way, write test
    return render_template('users/templates/profile.html', user=current_user)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


"""
ADD
"""


@app.route("/add/district", methods=['GET', 'POST'])
def add_district():
    form = AddDistrict()
    user = current_user
    if request.method == "POST":
        #Add district to db.
        db.session.add(District(form.name.data,
                                form.shortname.data,
                                form.base_path.data))
        db.session.commit()

    return render_template('add_district.html', form=form, user=user)


@login_required
@app.route("/add/school", methods=['GET', 'POST'])
def add_school():
    form = AddSchool()
    user = current_user
    msg = ""

    if request.method == "POST":
        #The district_id is supposed to be an integer
        #try:
            #district = District.query.filter_by(id=int(form.district_id))
            #                                          .all()
            #if len(district) == 1:
                #Add School to db
        db.session.add(School(int(form.district_id.data),
                              form.name.data, form.shortname.data,
                              form.domain.data. form.license.data))
        db.session.commit()
            #else:
            #    error_msg= "A district with that id doesn't exist!"
        #except:
        #    error_msg= "The entered district_id was not an integer!"
    return render_template('add_school.html', form=form,
                           msg=msg, user=user)


@app.route("/add/course", methods=['GET', 'POST'])
def add_course():
    form = AddCourse()
    user = current_user
    msg = ""
    if request.method == "POST":
        db.session.add(Course(int(form.serial.data), form.name.data,
                              form.shortname.data, form.license.data,
                              form.category.data))
        db.session.commit()
        msg = "Course: "+form.name.data+"added successfully!"

    return render_template('add_course.html', form=form, msg=msg, user=user)


"""
INSTALL
"""

@app.route('/get_site_by/<int:site_id>', methods=['GET'])
def site_by_id(site_id):
    address = Site.query.filter_by(id=site_id).first().baseurl
    return jsonify(address=address)

@app.route('/install/course', methods=['GET', 'POST'])
def install_course():
    """
    Displays a form for the admin user to pick courses to install on a site

    Returns:
        Rendered template
    """


    if request.method == 'GET':
        form = InstallCourse()

        # Query all moodle 2.2 courses
        courses = db.session.query(CourseDetail).filter(
                                   CourseDetail.moodle_version.like("2.5%")).all()


        # Query all moodle sites
        sites = Site.query.filter_by(sitetype='moodle').all()
        moodle_22_sites = []
        moodle_22_sites.append(Site.query.filter_by(id="503").first())

        # For all sites query the SiteDetail to see if it's a moodle 2.2 site
        for site in sites:
            details = db.session.query(SiteDetail) \
                                .filter(and_(SiteDetail.site_id == site.id,
                                             SiteDetail.siterelease
                                                       .like('2.2%'))) \
                                .order_by(SiteDetail.timemodified.desc()).first()

            if details:
                moodle_22_sites.append(site)

        # Generate the list of choices for the template
        courses_info = []
        sites_info = []

        listed_courses = []
        # Create the courses list
        for course in courses:
            if course.course_id not in listed_courses:
                if course.version:
                    courses_info.append((course.course_id,
                                        "%s - v%s" %
                                        (course.course.name, course.version)))
                else:
                    courses_info.append((course.course_id,
                                    "%s" %
                                    (course.course.name)))
                listed_courses.append(course.course_id)


        # Create the sites list
        for site in moodle_22_sites:
            sites_info.append((site.id, site.name))

        form.course.choices = sorted(courses_info, key=lambda x: x[1])
        form.site.choices = sorted(sites_info, key=lambda x: x[1])
        form.filter.choices = [(folder, folder) for folder in get_course_folders()]

        return render_template('install_course.html',
                               form=form, user=current_user)

    elif request.method == 'POST':
        # An array of unicode strings will be passed, they need to be integers
        # for the query
        selected_courses = [int(cid) for cid in request.form.getlist('course')]
        print selected_courses

        site_url = Site.query.filter_by(id=request.form.get('site')).first().baseurl

        # The site to install the courses
        site = "http://%s/webservice/rest/server.php?wstoken=%s&wsfunction=%s" % (
               site_url,
               app.config['INSTALL_COURSE_WS_TOKEN'],
               app.config['INSTALL_COURSE_WS_FUNCTION'])
        site = str(site.encode('utf-8'))

        # The CourseDetail objects needed to generate the url
        courses = []
        for cid in selected_courses:
            courses.append(CourseDetail.query.filter_by(course_id=cid)
                                             .order_by(CourseDetail.updated.desc())
                                             .first())

        # Course installation results
        output = ''

        # Loop through the courses, generate the command to be run, run it, and
        # append the ouput to output
        #
        # Currently this will break as our db is not setup correctly yet
        for course in courses:
            #Courses are detached from session for being inactive for too long.
            course.course.name
            resp = install_course_to_site.delay(course, site)

            output += "%s\n\n%s\n\n\n" % \
                      (course.course.name, resp)

        return render_template('install_course_output.html',
                               output=output,
                               user=current_user)

@app.route("/1/course/install", methods=["POST"])
def install_course_api():
    # An array of unicode strings will be passed, they need to be integers
    # for the query
    selected_courses = [int(cid) for cid in request.json.get('course_ids')]

    site_url = Site.query.filter_by(id=request.json.get('moodle_site_id')).first().baseurl

    # The site to install the courses
    site = "http://%s/webservice/rest/server.php?wstoken=%s&wsfunction=%s" % (
           site_url,
           app.config['INSTALL_COURSE_WS_TOKEN'],
           app.config['INSTALL_COURSE_WS_FUNCTION'])
    site = str(site.encode('utf-8'))

    # The CourseDetail objects needed to generate the url
    courses = CourseDetail.query.filter(CourseDetail
                                        .course_id.in_(selected_courses))\
                                .all()

    # Course installation results
    output = ''

    # Loop through the courses, generate the command to be run, run it, and
    # append the ouput to output
    #
    # Currently this will break as our db is not setup correctly yet
    for course in courses:
        #Courses are detached from session for being inactive for too long.
        course.course.name
        output += install_course_to_site.delay(course, site)

    return "The selected courses are now being installed!"

@celery.task(name='tasks.install_course')
def install_course_to_site(course, site):
    # To get the file path we need the text input, the lowercase of
    # source, and the filename
    fp = app.config['INSTALL_COURSE_FILE_PATH']
    fp += 'flvs/'

    data = {'filepath': fp,
            'file': course.filename,
            'courseid': course.course_id,
            'coursename': course.course.name,
            'shortname': course.course.shortname,
            'category': '1',
            'firstname': 'orvsd',
            'lastname': 'central',
            'city': 'none',
            'username': 'admin',
            'email': 'a@a.aa',
            'pass': 'adminpass'}

    resp = requests.post(site, data=data)

    # Unfortunately the response object to turned into unicode
    # when returned by the celery job, so we must send the
    # data we need, instead of the whole object.
    return resp.text

@app.route("/courses/filter", methods=["POST"])
def get_course_list():
    dir = request.form.get('filter')

    selected_courses = CourseDetail.query.all()
    if dir == "None":
        courses = selected_courses
    else:
        courses = [course for course in selected_courses if course.course.source == dir]

    # This means the folder selected was not the source folder or None.
    if not courses:
        courses = db.session.query(CourseDetail).filter(
                                   CourseDetail.filename.like("%"+dir+"%")).all()

    courses = sorted(courses, key=lambda x: x.course.name)
    serialized_courses = []
    for course in courses:
        serialized_courses.append(({'id' : course.course_id, 'name' : course.course.name}))

    return jsonify(courses=serialized_courses)


def get_course_folders():
    base_path = "/data/moodle2-masters/"
    folders = ['None']
    for root, sub_folders, files in os.walk(base_path):
        for folder in sub_folders:
            if folder not in folders:
                folders.append(folder)
    return folders

"""
VIEW
"""


@app.route('/view/schools/<int:school_id>', methods=['GET'])
@login_required
def view_school(school_id):

    school = School.query.filter_by(id=school_id).first()

    # Info for the school's page
    admins = 0
    teachers = 0
    users = 0

    # Get the school's sites
    sites = Site.query.filter_by(school_id=school_id).all()

    # School view template
    t = app.jinja_env.get_template('views/school.html')

    # if we have sites, grab the details needed for the template
    if sites:
        for site in sites:
            detail = SiteDetail.query.filter_by(site_id=site.id) \
                                     .order_by(SiteDetail
                                               .timemodified.desc()) \
                                     .first()
            if detail:
                admins += detail.adminusers
                teachers += detail.teachers
                users += detail.totalusers

    # Return a pre-compiled template to be dumped into the view template
    template = t.render(name=school.name, admins=admins, teachers=teachers,
                        users=users, user=current_user)

    return render_template('view.html', content=template, user=current_user)


# View courses installed for a specific school
@app.route('/view/schools/<int:school_id>/courses', methods=['GET'])
@login_required
def view_school_courses(school_id):
    school = School.query.filter_by(id=school_id).first()

    # Info for the school's page
    admins = 0
    teachers = 0
    users = 0

    # Get the school's sites
    sites = Site.query.filter_by(school_id=school_id).all()

    # School view template
    t = app.jinja_env.get_template('views/courses.html')

    # if we have sites, grab the details needed for the template
    if sites:
        for site in sites:
            detail = SiteDetail.query.filter_by(site_id=site.id) \
                                     .order_by(SiteDetail
                                               .timemodified.desc()) \
                                     .first()
            if detail:
                admins += detail.adminusers
                teachers += detail.teachers
                users += detail.totalusers

    # Get course list
    course_details = db.session.query("id", "status") \
    .from_statement("SELECT * "
    "FROM celery_taskmeta") \
    .all()

    # Return a pre-compiled template to be dumped into the view template
    template = t.render(name=school.name, admins=admins, teachers=teachers,
                        users=users, user=current_user, course_list=course_details)

    return render_template('view.html', content=template, user=current_user)


@app.route('/report/get_schools', methods=['POST'])
def get_schools():
    # From the POST, we need the district id, or distid
    dist_id = request.form.get('distid')

    # Given the distid, we get all the schools
    if dist_id:
        schools = School.query.filter_by(district_id=dist_id) \
                              .order_by("name").all()
    else:
        schools = School.query.order_by("name").all()

    # the dict to be jsonify'd
    school_list = {}

    for school in schools:
        school_list[school.shortname] = {'name': school.name, 'id': school.id}

    # Returned the jsonify'd data of counts and schools for jvascript to parse
    return jsonify(schools=school_list, counts=district_details(schools))


"""
REPORT
"""


@app.route("/report", methods=['GET'])
@login_required
def report():
    all_districts = District.query.order_by("name").all()
    dist_count = len(all_districts)
    school_count = School.query.count()
    site_count = Site.query.count()
    course_count = Course.query.count()

    inner = ""
    accord_id = "dist_accord"
    dist_id = "distid=%s"

    data = build_accordion(all_districts, accord_id, "district", dist_id)

    return render_template("report.html",
                           datadump=data,
                           dist_count=dist_count,
                           school_count=school_count,
                           site_count=site_count,
                           course_count=course_count,
                           user=current_user)


@app.route('/')
@login_required
def root():
    if not current_user.is_anonymous():
        return redirect(url_for('report'))
    return redirect(url_for('login'))


"""
UPDATE
"""

@app.route("/<category>/update")
@login_required
def update(category):
    obj = get_obj_by_category(category)

    identifier = get_obj_identifier(category)
    if 'details' in category:
        category = category.split("details")[0] + " " + "Details"
    category = category[0].upper() + category[1:]
    if obj:
        objects = obj.query.order_by(identifier).all()
        if objects:
            return render_template("update.html", objects=objects,
                                    identifier=identifier, user=current_user,
                                    category=category)

    abort(404)

@app.route("/<category>/<id>", methods=["GET"])
def get_object(category, id):
    obj = get_obj_by_category(category)
    if obj:
        modified_obj = obj.query.filter_by(id=id).first()
        if modified_obj:
            return jsonify(modified_obj.serialize())

    abort(404)

@app.route("/<category>/<id>/update", methods=["POST"])
def update_object(category, id):
    obj = get_obj_by_category(category)
    if obj:
        modified_obj = obj.query.filter_by(id=request.form.get("id")).first()
        if modified_obj:
            inputs = {}
            # Here we update our dict with new
            [inputs.update( {key : string_to_type(request.form.get(key))})
                        for key in modified_obj.serialize().keys()]

            db.session.query(obj).filter_by(id=request.form.get("id")).update(inputs)
            db.session.commit()
            return "Object updated sucessfully!"

    abort(404)

@app.route("/<category>/<id>/delete", methods=["POST"])
def delete_object(category, id):
    obj = get_obj_by_category(category)
    if obj:
        modified_obj = obj.query.filter_by(id=request.form.get("id")).first()
        if modified_obj:
            db.session.delete(modified_obj)
            db.session.commit()
            return "Object deleted successful!"

    abort(404)



def string_to_type(string):
    # Have to watch out for the format of true/false/null
    # with javascript strings.
    if string == "true":
        return True
    elif string == "false":
        return False
    elif string == "null":
        return None
    try:
        return float(string)
    except ValueError:
        if string.isdigit():
            return int(string)
    return string
"""
REMOVE
"""


@app.route("/display/<category>")
def remove(category):
    user = get_user()
    obj = get_obj_by_category(category)
    if obj:
        objects = obj.query.all()
        if objects:
            # fancy way to get the properties of an object
            properties = objects[0].get_properties()
            return render_template('removal.html', category=category,
                                   objects=objects, properties=properties,
                                   user=user)

    abort(404)


@app.route("/remove/<category>", methods=['POST'])
def remove_objects(category):
    obj = get_obj_by_category(category)
    remove_ids = request.form.getlist('remove')
    for remove_id in remove_ids:
        # obj.query returns a list, but should only have one element because
        # ids are unique.
        remove = obj.query.filter_by(id=remove_id)[0]
        db.session.delete(remove)

    db.session.commit()

    return redirect('display/'+category)


"""
HELPERS
"""


@login_manager.unauthorized_handler
def unauthorized():
    flash('You are not authorized to view this page, please login.')
    return redirect('/login')


@login_manager.user_loader
def load_user(userid):
    return User.query.filter_by(id=userid).first()


@google.tokengetter
def get_access_token():
    return session.get('access_token')


def build_accordion(objects, accordion_id, type, extra=None):
    inner_t = app.jinja_env.get_template('accordion_inner.html')
    outer_t = app.jinja_env.get_template('accordion.html')

    inner = ""

    for obj in objects:
        inner += inner_t.render(accordion_id=accordion_id,
                                inner_id=obj.shortname,
                                type=type,
                                link=obj.name,
                                extra=None if not extra else extra % obj.id)

    return outer_t.render(accordion_id=accordion_id,
                          dump=inner)


def get_obj_by_category(category):
    # Checking for case insensitive categories
    categories = {'districts': District, 'schools': School,
                  'sites': Site, 'courses': Course, 'users': User,
                  'sitedetails':SiteDetail, 'coursedetails': CourseDetail}

    return categories.get(category.lower())

def get_obj_identifier(category):
    categories = {'districts': 'name', 'schools': 'name',
                  'sites': 'name', 'courses': 'name', 'users': 'name',
                  'sitedetails': 'site_id', 'coursedetails': 'filename'}

    return categories.get(category.lower())

def get_user():
    # A user id is sent in, to check against the session
    # and based on the result of querying that id we
    # return a user (whether it be a sqlachemy obj or an
    # obj named guest

    if 'user_id' in session:
            return User.query.filter_by(id=session["user_id"]).first()


def district_details(schools):
    """
    district_details adds up the number of teachers, users, and admins of all
    the district's school's sites.

    Args:
        schools (list): list of schools to total the users, teachers, and
         admins.

    Returns:
        dict. The total admins, teachers, and users of the schools
    """

    admin_count = 0
    teacher_count = 0
    user_count = 0

    for school in schools:
        sites = Site.query.filter_by(school_id=school.id).all()
        for site in sites:
            details = SiteDetail.query.filter_by(site_id=site.id) \
                                      .order_by(SiteDetail
                                                .timemodified
                                                .desc()) \
                                      .first()
            if details:
                admin_count += details.adminusers
                teacher_count += details.teachers
                user_count += details.totalusers

    return {'admins': admin_count,
            'teachers': teacher_count,
            'users': user_count}

"""
API
"""

@app.route("/1/site/<site_id>/courses")
def get_courses_by_site(site_id):
    #SiteDetails hold the course information we are looking for
    site_details = SiteDetail.query.filter_by(site_id=site_id) \
                                   .order_by(SiteDetail
                                             .timemodified
                                             .desc()) \
                                   .first()

    return jsonify(content=json.loads(site_details.courses))

@app.route("/1/sites/<baseurl>/moodle")
def get_moodle_sites(baseurl):
    school_id = Site.query.filter_by(baseurl=baseurl).first().school_id
    moodle_sites = Site.query.filter_by(school_id=school_id).all()
    data = [{'id': site.id, 'name': site.name} for site in moodle_sites]
    return jsonify(content=data)

@app.route('/celery/status/<celery_id>')
def get_task_status(celery_id):
    status = db.session.query("status") \
                       .from_statement("SELECT status "
                           "FROM celery_taskmeta WHERE id=:celery_id") \
                           .params(celery_id=celery_id).first()
    return jsonify(status=status)

@app.route("/1/sites/<baseurl>")
def get_site_by_url(baseurl):
    site = Site.query.filter_by(baseurl=baseurl).first()
    if site:
        site_details = SiteDetail.query.filter_by(site_id=site.id) \
                                       .order_by(SiteDetail
                                                 .timemodified
                                                 .desc()) \
                                       .first()

        site_info = dict(site.serialize().items() + \
                    site_details.serialize().items())

        return jsonify(content=site_info)
    return jsonify(content={'error': 'Site not found'})

#TODO:
'''
1. Comment more
2. Separate into seperate functions
'''
@app.route("/courses/list/update", methods=['GET', 'POST'])
def update_courselist():
    """
        Updates the database to contain the most recent course
        and course detail entries, based on available files.
    """
    num_courses = 0
    base_path = "/data/moodle2-masters/"
    mdl_files = []
    if request.method == "POST":
        # Get a list of all moodle course files
        # for source in os.listdir(base_path):
        for root, sub_folders, files in os.walk(base_path):
            for file in files:
                full_file_path = os.path.join(root, file)
                if os.path.isfile(full_file_path):
                    mdl_files.append(full_file_path)

        filenames = []
        sources = []
        for filename in mdl_files:
            source, path = get_path_and_source(base_path, filename)
            sources.append(source)
            filenames.append(path)

        details = db.session.query(CourseDetail) \
                        .join(CourseDetail.course) \
                        .filter(CourseDetail.filename.in_(
                            filenames)).all()

        for detail in details:
            if detail.filename in filenames:
                sources.pop(filenames.index(detail.filename))
                filenames.pop(filenames.index(detail.filename))

        for source, file_path in zip(sources, filenames):
            create_course_from_moodle_backup(base_path, source, file_path)
            num_courses += 1

        if num_courses > 0:
            flash(str(num_courses) + ' new courses added successfully!')
    return render_template('update_courses.html', user=current_user)

# /base_path/source/path is the format of the parsed directories.
def get_path_and_source(base_path, file_path):
    path = file_path.strip(base_path).partition('/')
    return path[0]+'/', path[2]

def create_course_from_moodle_backup(base_path, source, file_path):
    # Needed to delete extracted xml once operation is done
    project_folder = "/home/vagrant/orvsd_central/"

    # Unzip the file to get the manifest (All course backups are zip files)
    zip = zipfile.ZipFile(base_path+source+file_path)
    xmlfile = file(zip.extract("moodle_backup.xml"), "r")
    xml = Soup(xmlfile.read(), "lxml")
    info = xml.moodle_backup.information
    old_course = Course.query.filter_by(
                    name=info.original_course_fullname.string) or \
                 Course.query.filter_by(
                    name=info.original_course_shortname.string)

    if old_course is not None:
        # Create a course since one is unable to be found with that name.
        new_course = Course(serial=1000 + len(Course.query.all()),
                            source=source.replace('/', ''),
                            name=info.original_course_fullname.string,
                            shortname=info.original_course_shortname.string)
        db.session.add(new_course)

        # Until the session is committed, the new_course does not yet have
        # an id.
        db.session.commit()

        course_id = new_course.id
    else:
        course_id = old_course.id

    _version_re = re.findall(r'_v(\d)_', file_path)

    # Regex will only be a list if it has a value in it
    version = _version_re[0] if list(_version_re) else None

    new_course_detail = CourseDetail(course_id=course_id,
                                         filename=file_path,
                                         version=version,
                                         updated=datetime.datetime.now(),
                                         active=True,
                                         moodle_version=info.moodle_release.string,
                                         moodle_course_id=info.original_course_id.string)

    db.session.add(new_course_detail)
    db.session.commit()

    #Get rid of moodle_backup.xml
    os.remove(project_folder+"moodle_backup.xml")

# Get all task IDs
# TODO: Needs testing
@app.route('/celery/id/all')
def get_all_ids():
    # TODO: "result" is another column, but SQLAlchemy complains of some encoding error.
    statuses = db.session.query("id", "task_id", "status", "date_done", "traceback") \
                       .from_statement("SELECT * "
                           "FROM celery_taskmeta") \
                           .all()

    return jsonify(status=statuses)
