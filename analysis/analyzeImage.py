import astropy.wcs
import astropy.units as u
import astropy.coordinates as astroCoords
import numpy as np
import matplotlib.mlab as mlab
import astropy.convolution as conv
import matplotlib.pyplot as plt
from createImage import createImage as ci
from astropy.io import fits
from scipy.spatial.distance import euclidean
from sklearn.cluster import DBSCAN
from skimage import measure
#from pathos.multiprocessing import ProcessPool as Pool
from pathos.threading import ThreadPool as Pool


class analyzeImage(object):

    def calcArrayLimits(self, imShape, centerX, centerY, scaleFactor, sigmaArr):

        xmin = int(centerX-(scaleFactor*sigmaArr[0]))
        xmax = int(1+centerX+(scaleFactor*sigmaArr[0]))
        ymin = int(centerY-(scaleFactor*sigmaArr[1]))
        ymax = int(1+centerY+(scaleFactor*sigmaArr[1]))
        if ((xmin < 0) | (ymin < 0) | (xmax >= imShape[0]) | (ymax >= imShape[1])):
            maxXOff = xmax-imShape[0]+1
            maxYOff = ymax-imShape[1]+1
            minXOff = xmin*(-1.)
            minYOff = ymin*(-1.)
            offset = np.max([maxXOff, maxYOff, minXOff, minYOff])
            xmin += offset
            xmax -= offset
            ymin += offset
            ymax -= offset
        else:
            offset = None

        return xmin, xmax, ymin, ymax, offset

    def createAperture(self, imShape, locationArray, radius, mask=False):

        """
        Create a circular aperture for an image. Aperture area will be 1's
        and all area outside will be 0's. Just multiply aperture by image to get
        everything outside aperture masked out.

        Parameters
        ----------

        imShape: list, [2], required
        The row, column dimensions of the image.

        locationArray: list, [Nx2], required
        The locations in the image where apertures should be centered.

        radius: float, required
        The radius of the circular aperture in pixels.

        mask: boolean, optional, default=False
        If true, then aperture area inside is set to 0's and outside to 1's
        making this a mask of the area instead.

        Returns
        -------

        apertureArray: numpy array
        Array of the same size as imShape but with 1's inside the aperture and
        0's outside unless mask is set to True then it is the opposite.
        """
        apertureArray = np.zeros((imShape))

        if len(np.shape(locationArray)) < 2:
            locationArray = [locationArray]

        for center in locationArray:
            centerX = center[0]
            centerY = center[1]
            for ix in range(0, int(imShape[0])):
                for iy in range(0, int(imShape[1])):
                    distX = centerX - ix
                    distY = centerY - iy
                    if np.sqrt((distX**2)+(distY**2)) <= radius:
                        apertureArray[ix, iy] = 1.

        if mask==True:
            apertureArray -= 1
            apertureArray = np.abs(apertureArray)

        return apertureArray

    def trackSingleObject(self, imageArray, gaussSigma):

        objectCoords = []
        for image in imageArray:
            newImage = createImage().convolveGaussian(image, gaussSigma)
            maxIdx = np.argmax(newImage)
            objectCoords.append(np.unravel_index(maxIdx, np.shape(newImage)))
        return objectCoords

    def plotSingleTrajectory(self, imageArray, gaussSigma):

        objCoords = self.trackSingleObject(imageArray, gaussSigma)
        fig = plt.figure(figsize=(12,12))
        plt.plot(np.array(objCoords)[:,0], np.array(objCoords)[:,1], '-ko')
        plt.xlim((0, np.shape(imageArray[0])[0]))
        plt.ylim((0, np.shape(imageArray[0])[1]))

        return fig

    def calcSNR(self, image, centerArr, gaussSigma, background, imSize, apertureScale=1.6):

        if isinstance(background, np.ndarray):
            backgroundArray = background
        else:
            backgroundArray = np.ones((imSize))*background

        apertureScale = 1.6 #See derivation here: http://wise2.ipac.caltech.edu/staff/fmasci/GaussApRadius.pdf
        aperture = self.createAperture(imSize, centerArr, apertureScale*gaussSigma[0])
        sourceCounts = np.sum(image*aperture)
        print sourceCounts
        if sourceCounts < 0:
            sourceCounts = 0.0
        noiseCounts = np.sum(backgroundArray*aperture)
        print noiseCounts

        snr = sourceCounts/np.sqrt(noiseCounts)
        #snr = sourceCounts/np.sqrt(sourceCounts+noiseCounts)
        return snr

    def calcTheorySNR(self, sourceFlux, centerArr, gaussSigma, background, imSize, apertureScale=1.6):

        if isinstance(background, np.ndarray):
            backgroundArray = background
        else:
            backgroundArray = np.ones((imSize))*background

        sourceTemplate = createImage().createGaussianSource(centerArr, gaussSigma, imSize, sourceFlux)

        aperture = self.createAperture(imSize, centerArr, apertureScale, gaussSigma[0])
        sourceCounts = np.sum(sourceTemplate*aperture)
        noiseCounts = np.sum(backgroundArray*aperture)

        snr = sourceCounts/np.sqrt(sourceCounts+noiseCounts)
        return snr

    def addMask(self, imageArray, locations, gaussSigma):

        maskedArray = np.zeros((np.shape(imageArray)))
        scaleFactor = 4.
        i = 0
        for image in imageArray:
            maskedArray[i] = image * self.createAperture(np.shape(image), locations, scaleFactor, gaussSigma, mask=True)
            i+=1

        return maskedArray

    def return_ra_dec(self, t0_pos, t0_vel, image_times, t0_mjd, wcs,
                      position_error, telescope_code):

        """
        Return a set of ra and dec coordinates for a trajectory.
        Used as input into Bernstein and Khushalani (2000) orbit fitting
        code found here: http://www.physics.upenn.edu/~garyb/#software


        Parameters
        ----------

        t0_pos: numpy array, [2], required
        The starting x,y pixel location

        t0_vel: numpy array, [2], required
        The x,y velocity of the object in pixels/hr.

        image_times: numpy array, required
        An array containing the image times in hours with the first image at
        time 0.

        t0_mjd: numpy array, required
        The MJD times of each image.

        wcs: astropy.wcs.wcs instance, required
        The astropy.wcs instance of the first image.

        position_error: numpy array, required
        The position error in the observations in arcsec.

        telescope_code: int, required
        The telescope code for Bernstein and Khushalani (2000)
        orbit fitting software. (Subaru is 568).

        Returns
        -------

        ra_dec_coords: numpy array
        Array of strings with the (mjd, ra, dec,
        position_error, telescope_code) for each image in the trajectory.
        """

        pixel_vals = []
        for time_pt in image_times:
            pixel_vals.append(t0_pos + t0_vel*time_pt)
        pixel_vals = np.array(pixel_vals)
        coord_vals = astroCoords.SkyCoord.from_pixel(pixel_vals[:,0], pixel_vals[:,1], wcs)
        coord_list = coord_vals.to_string('hmsdms')
        output_list = []
        for coord_val, mjd, err_val in zip(coord_list, t0_mjd, position_error):
            coord_ra, coord_dec = coord_val.split(' ')
            ra_h = coord_ra.split('h')[0]
            ra_m = coord_ra.split('m')[0].split('h')[1]
            ra_s = str('%.4f') % float(coord_ra.split('s')[0].split('m')[1])
            dec_d = coord_dec.split('d')[0]
            dec_m = coord_dec.split('m')[0].split('d')[1]
            dec_s = str('%.4f') % float(coord_dec.split('s')[0].split('m')[1])
            output_list.append(str('%.4f' + '  ' + '%s:%s:%s' + '  ' + '%s:%s:%s' +
                                   '  ' + '%.2f  %i') % (mjd+2400000.5, ra_h, ra_m,
                                                         ra_s, dec_d, dec_m, dec_s,
                                                         err_val, telescope_code))
        ra_dec_coords = np.array(output_list, dtype=str)

        return ra_dec_coords

    def createPostageStamp(self, imageArray, objectStartArr, velArr,
                           timeArr, stamp_width):

        """
        Create postage stamp image coadds of potential objects traveling along
        a trajectory.

        Parameters
        ----------

        imageArray: numpy array, required
        The masked input images.

        objectStartArr: numpy array, required
        An array with the starting location of the object in pixels.

        velArr: numpy array, required
        The x,y velocity in pixels/hr. of the object trajectory.

        timeArr: numpy array, required
        The time in hours of each image starting from 0 at the first image.

        stamp_width: numpy array or list, [2], required
        The row, column dimensions of the desired output image.

        Returns
        -------

        stampImage: numpy array
        The coadded postage stamp.

        singleImagesArray: numpy array
        The postage stamps that were added together to create the coadd.
        """

        singleImagesArray = []
        stampWidth = np.array(stamp_width, dtype=int)
        #print stampWidth
        stampImage = np.zeros(stampWidth)
        if len(np.shape(imageArray)) < 3:
            imageArray = [imageArray]

        measureCoords = ci().calcCenters(np.array(objectStartArr), np.array(velArr), timeArr)

        if len(np.shape(measureCoords)) < 2:
            measureCoords = [measureCoords]
        off_edge = []
        for centerCoords in measureCoords:
            if (centerCoords[0] + stampWidth[0]/2 + 1) > np.shape(imageArray[0])[1]:
                #raise ValueError('The boundaries of your postage stamp for one of the images go off the edge')
                off_edge.append(True)
            elif (centerCoords[0] - stampWidth[0]/2) < 0:
                #raise ValueError('The boundaries of your postage stamp for one of the images go off the edge')
                off_edge.append(True)
            elif (centerCoords[1] + stampWidth[1]/2 + 1) > np.shape(imageArray[0])[0]:
                #raise ValueError('The boundaries of your postage stamp for one of the images go off the edge')
                off_edge.append(True)
            elif (centerCoords[1] - stampWidth[1]/2) < 0:
                #raise ValueError('The boundaries of your postage stamp for one of the images go off the edge')
                off_edge.append(True)
            else:
                off_edge.append(False)

        i=0
        for image in imageArray:
            if off_edge[i] is False:
                xmin = int(np.rint(measureCoords[i,1]-stampWidth[0]/2))
                xmax = int(xmin + stampWidth[0])
                ymin = int(np.rint(measureCoords[i,0]-stampWidth[1]/2))
                ymax = int(ymin + stampWidth[1])
                #print xmin, xmax, ymin, ymax
                single_stamp = image[xmin:xmax, ymin:ymax]
                single_stamp[np.isnan(single_stamp)] = 0.
                single_stamp[np.isinf(single_stamp)] = 0.
                stampImage += single_stamp
                singleImagesArray.append(single_stamp)
            else:
                single_stamp = np.zeros((stampWidth))
                singleImagesArray.append(single_stamp)

            i+=1
        return stampImage, singleImagesArray

    def plotTrajectory(self, results_arr, image_times, raw_im,
                       im_plot_args=None, traj_plot_args=None):

        """
        Plot an object's trajectory along a section of one of the
        original masked images.

        Parameters
        ----------

        results_arr: numpy recarray, required
        The results output from findObjects in searchImage.

        image_times: numpy array, required
        An array containing the image times in hours with the first image at
        time 0.

        raw_im: numpy array, required
        One of the masked original images. See loadMaskedImages
        in searchImage.py.

        im_plot_args: dict, optional
        Plotting arguments for the masked image.

        traj_plot_args: dict, optional
        Scatter plot arguments for the trajectory on top of masked image.

        Returns
        -------

        ax: matplotlib axes instance
        Returns instance after plt.imshow and plt.plot
        """

        t0_pos = [results_arr['t0_x'], results_arr['t0_y']]
        pixel_vel = [results_arr['v_x'], results_arr['v_y']]
        coords = [np.array(t0_pos) +
                  np.array([pixel_vel[0]*it, pixel_vel[1]*it])
                  for it in image_times]
        coords = np.array(coords)

        default_im_plot_args = {'cmap': 'Greys_r', 'origin': 'lower'}
        default_traj_plot_args = {'marker': 'o', 'c': 'r'}

        if im_plot_args is not None:
            default_im_plot_args.update(im_plot_args)
        im_plot_args = default_im_plot_args

        if traj_plot_args is not None:
            default_traj_plot_args.update(traj_plot_args)
        traj_plot_args = default_traj_plot_args

        ax = plt.gca()
        plt.imshow(raw_im, **im_plot_args)
        plt.plot(coords[:, 0], coords[:, 1], **traj_plot_args)
        plt.xlim((t0_pos[0]-25, t0_pos[0]+75))
        plt.ylim((t0_pos[1]-25, t0_pos[1]+75))
        return ax

    def plotLightCurves(self, im_array, results_arr, image_times):

        """
        Plots light curve of trajectory using array of masked images.

        Parameters
        ----------
        im_array: numpy array, required
        The masked original images. See loadMaskedImages
        in searchImage.py.

        results_arr: numpy recarray, required
        The results output from findObjects in searchImage.

        image_times: numpy array, required
        An array containing the image times in hours with the first image at
        time 0.

        Returns
        -------
        ax: matplotlib axes instance
        The axes instance where the plt.plot of the lightcurve was drawn.
        """

        coords = self.calc_traj_coords(results_arr, image_times)
        aperture = self.createAperture([11,11], [5., 5.],
                                       1., mask=False)

        ax = plt.gca()
        #plt.plot(image_times, [np.sum(im_array[x][coords[x,1]-5:coords[x,1]+6,
        #                                          coords[x,0]-5:coords[x,0]+6]*aperture)
        plt.plot(image_times, [im_array[x][coords[x,1], coords[x,0]]
                               for x in range(0, len(image_times))], '-o')
        ax.set_xlabel('Time (days)')
        ax.set_ylabel('Flux')
        return ax

    def calc_traj_coords(self, results_arr, image_times):

        """
        Calculate the image coordinates of the trajectory of an object.

        Parameters
        ----------
        results_arr: numpy recarray, required
        The results output from findObjects in searchImage. 

        image_times: numpy array, required
        An array containing the image times in hours with the first image at
        time 0.

        Returns
        -------
        traj_coords: numpy array
        The x,y coordinates of the trajectory in each image.
        """

        t0_pos = [results_arr['t0_x'], results_arr['t0_y']]
        pixel_vel = [results_arr['v_x'], results_arr['v_y']]
        coords = [np.array(t0_pos) +
                  np.array([pixel_vel[0]*it, pixel_vel[1]*it])
                  for it in image_times]
        traj_coords = np.array(coords, dtype=int)

        return traj_coords
        
    def clusterResults(self, results, dbscan_args=None):

        """
        Use scikit-learn algorithm of density-based spatial clustering of
        applications with noise (DBSCAN)
        (http://scikit-learn.org/stable/modules/generated/
            sklearn.cluster.DBSCAN.html)
        to cluster the results of the likelihood image search using starting
        location, total velocity and slope of trajectory.

        Parameters
        ----------

        results: numpy recarray, required
        The results output from findObjects in searchImage.

        dbscan_args: dict, optional
        Additional arguments for the DBSCAN instance. See options in link
        above.

        Returns
        -------

        db_cluster: DBSCAN instance
        DBSCAN instance with clustering completed. To get cluster labels use
        db_cluster.labels_

        top_vals: list of integers
        The indices in the results array where the most likely object in each
        cluster is located.
        """

        default_dbscan_args = dict(eps=0.1, min_samples=1)

        if dbscan_args is not None:
            default_dbscan_args.update(dbscan_args)
        dbscan_args = default_dbscan_args

        slope_arr = []
        intercept_arr = []
        t0x_arr = []
        t0y_arr = []
        vel_total_arr = []
        vx_arr = []
        vel_x_arr = []
        vel_y_arr = []
        for target_num in range(len(results)):

            t0x = results['t0_x'][target_num]
            t0x_arr.append(t0x)
            t0y = results['t0_y'][target_num]
            t0y_arr.append(t0y)
            v0x = results['v_x'][target_num]
            vel_x_arr.append(v0x)
            v0y = results['v_y'][target_num]
            vel_y_arr.append(v0y)

        db_cluster = DBSCAN(**dbscan_args)

        scaled_t0x = t0x_arr - np.min(t0x_arr)
        if np.max(scaled_t0x) > 0.:
            scaled_t0x = scaled_t0x/np.max(scaled_t0x)
        scaled_t0y = t0y_arr - np.min(t0y_arr)
        if np.max(scaled_t0y) > 0.:
            scaled_t0y = scaled_t0y/np.max(scaled_t0y)
        scaled_vx = vel_x_arr - np.min(vel_x_arr)
        if np.max(scaled_vx) > 0.:
            scaled_vx /= np.max(scaled_vx)
        scaled_vy = vel_y_arr - np.min(vel_y_arr)
        if np.max(scaled_vy) > 0.:
            scaled_vy /= np.max(scaled_vy)

        db_cluster.fit(np.array([scaled_t0x, scaled_t0y,
                                 scaled_vx, scaled_vy
                                ], dtype=np.float).T)

        top_vals = []
        for cluster_num in np.unique(db_cluster.labels_):
            cluster_vals = np.where(db_cluster.labels_ == cluster_num)[0]
            top_vals.append(cluster_vals[0])

        return db_cluster, top_vals

    def filter_results(self, im_array, results, image_times, model, psf_sigma=1.0,
                       batch_size = 32, chunk_size = 10000):

        """
        Use a keras neural network model to detect real objects based upon
        the coadded postage stamps of those objects. Filter and keep only
        actual objects going forward.

        Parameters
        ----------

        im_array: numpy array, required
        The masked original images. See loadMaskedImages
        in searchImage.py.

        results_arr: numpy recarray, required
        The results output from findObjects in searchImage.

        image_times: numpy array, required
        An array containing the image times in DAYS with the first image at
        time 0.
        Note: This is different than other methods so the  units of 
        this may change. Watch this documentation.

        model: keras model, required
        A previously trained model loaded from an hdf5 file.

        batch_size: int
        Batch size for keras predict.

        Returns
        -------

        filtered_results: numpy array
        An edited version of results_arr with only the rows where 
        true objects were classified.
        
        """

        keep_objects = np.array([])
        total_chunks = np.ceil(len(results)/float(chunk_size))
        chunk_num = 1
        circle_vals = []
 
        enumerated_results = list(enumerate(results))
        self.im_array = im_array
        self.image_times = image_times
        self.psf_sigma = psf_sigma

#        for chunk_start in range(0, len(results), chunk_size):
#            test_class = []
#            p_stamp_arr = []
#            #circle_chunk = []
#            for imNum in range(chunk_start, chunk_start+chunk_size):
#                try:
#                    p_stamp = self.createPostageStamp(im_array, 
#                                                      list(results[['t0_x', 't0_y']][imNum]),
#                                                      np.array(list(results[['v_x', 'v_y']][imNum])),
#                                                      image_times, [25., 25.])[0]
#                    p_stamp = np.array(p_stamp)
#                    p_stamp[np.isnan(p_stamp)] = 0.
#                    p_stamp[np.isinf(p_stamp)] = 0.
#                    #p_stamp -= np.min(p_stamp)
#                    #p_stamp /= np.max(p_stamp)
#                    #p_stamp
#                    image_thresh = np.max(p_stamp)*0.5
#                    image = (p_stamp > image_thresh)*1.
#                    #pre_image = p_stamp > image_thresh
#                    #image = np.array(pre_image*1.)
#                    mom = measure.moments(image)
#                    cr = mom[0,1]/mom[0,0]
#                    cc = mom[1,0]/mom[0,0]
#                    #moments = measure.moments(image, order=3)
#                    #cr = moments[0,1]/moments[0,0]
#                    #cc = moments[1,0]/moments[0,0]
#                    cent_mom = measure.moments_central(image, cr, cc, order=4)
#                    norm_mom = measure.moments_normalized(cent_mom)
#                    hu_mom = measure.moments_hu(norm_mom)
#                    #p_stamp_arr.append(hu_mom)
#                    #print moments[0,0], measure.perimeter(image)
#                    #circularity = (4*np.pi*moments[0,0])/(measure.perimeter(image)**2.)
#                    #circularity = (cent_mom[0,0]**2.)/(2.*np.pi*(cent_mom[2,0] + cent_mom[0,2]))
#                    circularity = (1/(2.*np.pi))*(1/hu_mom[0])
#                    #circularity = (cent_mom[0,0]**2.)/(2*np.pi*(cent_mom[2,0] + cent_mom[0,2]))
#                    psf_sigma = psf_sigma
#                    gaussian_fwhm = psf_sigma*2.35
#                    fwhm_area = np.pi*(gaussian_fwhm/2.)**2.
#                    #print circularity, cr, cc
#                    if ((circularity > 0.6) & (cr > 10.) & (cr < 14.) & (cc > 10.) & (cc < 14.) &
#                        (cent_mom[0,0] < (9.0*fwhm_area)) & (cent_mom[0,0] > 3.0)): #Use 200% error margin on psf_sigma for now
#                    #    test_class.append(1.)
#                    #    print circularity, cr, cc, moments[0,0]
#                    #else:
#                    #    test_class.append(0.)
#                        test_class.append(1.)
#                    else:
#                        test_class.append(0.)
#                    circle_vals.append([circularity, cr, cc, cent_mom[0,0], image_thresh])
#                    #print circularity, cr, cc, cent_mom[0,0], image_thresh
#                except:
#                    #p_stamp_arr.append(np.ones((25, 25)))
#                    p_stamp_arr.append(np.zeros(7))
#                    test_class.append(0.)
#                    circle_vals.append([0., 0., 0., 0., 0.])
#                    continue
#            p_stamp_arr = np.array(p_stamp_arr)#.reshape(chunk_size, 625)
            #test_class = model.predict_classes(p_stamp_arr, batch_size=batch_size, 
            #                                   verbose=1)
        pool = Pool(nodes=8)
        test_classes = pool.map(self.circularity_test, enumerated_results)
        test_classes = np.array(test_classes).T
        keep_idx = test_classes[0][np.where(np.array(test_classes[1]) > .5)]# + chunk_start
        print keep_idx
        #print np.where(np.array(test_class) > .5)
        print test_classes[0][np.where(np.array(test_classes[1]) > .5)]
        keep_objects = keep_idx#np.append(keep_objects, keep_idx)
        #circle_vals[keep_idx] = np.array(circle_chunk)
        print "Finished chunk %i of %i" % (chunk_num, total_chunks)
        chunk_num += 1

#        keep_objects = np.arange(len(results))
        filtered_results = results[np.array(keep_objects, dtype=np.int)]
        #circle_vals = np.array(circle_vals)
        #circle_vals_keep = circle_vals[np.array(keep_objects, dtype=np.int)]

        return filtered_results#, circle_vals_keep

    def circularity_test(self, result_row):#, im_array, image_times, psf_sigma):

        im_array = self.im_array
        if result_row[0] % 5000 == 0.:
            print result_row[0]
        
        try:
            p_stamp = self.createPostageStamp(im_array,
                                              list([result_row[1]['t0_x'],
                                                    result_row[1]['t0_y']]),
                                              np.array(list([result_row[1]['v_x'],
                                                             result_row[1]['v_y']])),
                                              self.image_times, [25., 25.])[0]
            p_stamp = np.array(p_stamp)
            p_stamp[np.isnan(p_stamp)] = 0.
            p_stamp[np.isinf(p_stamp)] = 0.
            #p_stamp -= np.min(p_stamp)
            #p_stamp /= np.max(p_stamp)
            #p_stamp
            image_thresh = np.max(p_stamp)*0.5
            image = (p_stamp > image_thresh)*1.
            rprop = measure.regionprops(np.array(image, dtype=np.int), intensity_image=p_stamp)[0]    
            label_test, max_label = measure.label(image, return_num=True)
            max_conn = 0
            keep_label = 1
            for label_num in range(1, max_label):
                if len(np.where(label_test == label_num)[0]) > max_conn:
                    max_conn = len(np.where(label_test == label_num)[0])
                    keep_label = label_num
            image = (label_test == keep_label)*1.
            #pre_image = p_stamp > image_thresh
            #image = np.array(pre_image*1.)
            mom = measure.moments(image)
            if mom[0,0] > 0.:
                cr = mom[0,1]/mom[0,0]
                cc = mom[1,0]/mom[0,0]
                #cr = 12
                #cc = 12
                #moments = measure.moments(image, order=3)
                #cr = moments[0,1]/moments[0,0]
                #cc = moments[1,0]/moments[0,0]
                cent_mom = measure.moments_central(image, cr, cc, order=4)
                norm_mom = measure.moments_normalized(cent_mom)
                hu_mom = measure.moments_hu(norm_mom)
                #p_stamp_arr.append(hu_mom)
                #print moments[0,0], measure.perimeter(image)
                #circularity = (4*np.pi*moments[0,0])/(measure.perimeter(image)**2.)
                #circularity = (cent_mom[0,0]**2.)/(2.*np.pi*(cent_mom[2,0] + cent_mom[0,2]))
                if hu_mom[0] > 0.:
                #if rprop['perimeter'] > 0.:
                    circularity = (1/(2.*np.pi))*(1/hu_mom[0])
                #                    circularity = (1/(2.*np.pi))*(1/rprop['weighted_moments_hu'][0])
                #    circularity = (4*np.pi*rprop['area'])/(rprop['perimeter']**2.)
                else:
                    circularity = 0.
            else:
                circularity = 0.
            #print result_row[0], circularity
            #circularity = (cent_mom[0,0]**2.)/(2*np.pi*(cent_mom[2,0] + cent_mom[0,2]))
            psf_sigma = self.psf_sigma
            gaussian_fwhm = psf_sigma*2.35
            fwhm_area = np.pi*(gaussian_fwhm/2.)**2.
            wcr, wcc = rprop['weighted_centroid']

            if ((circularity > 0.7) & (cr > 10.) & (cr < 14.) & (cc > 10.) & (cc < 14.) &
#            if ((circularity > 0.4) & (circularity < 4.) & (cr > 10.) & (cr < 14.) & (cc > 10.) & (cc < 14.) &
                (cent_mom[0,0] < (9.0*fwhm_area)) & (cent_mom[0,0] > 4.0)): #Use 200% error margin on psf_sigma for now
                #    test_class.append(1.)
#                print circularity, cr, cc, cent_mom[0,0]
                #else:
                #    test_class.append(0.)
                test_class = 1.
                #print circularity, cr, cc, cent_mom[0,0]
            else:
                test_class = 0.

        except:
            test_class = 0.

        return [result_row[0], test_class]
